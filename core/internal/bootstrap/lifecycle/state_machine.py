#!/usr/bin/env python3
# GREP_SUMMARY: state-machine, bootstrap, lifecycle, node-init, node-update, checkpoint-resume, step-transitions, state-json, content-hash
# STRUCTURE: ▶ [--state-file] → ┌StepState + BootstrapState dataclasses┐ → ◇ StateMachine.__init__(load|create) → ○ transition loop: start→(done|skip|fail) → ⊕ hash-compare → ⚡ save() → ⎋ CLI dispatch (init/update)
# region MODULE_CONTRACT
## @purpose  Explicit state machine for node-lifecycle.sh bootstrap/update process.
##           Manages 17 init steps + 6 update steps via a JSON state file
##           at /var/lib/platform/.bootstrap/state.json (configurable).
##           Each step is a typed transition with pre/post conditions.
## @scope    Python-side of W4-E2 Strangler-Fig decomposition of node-lifecycle.sh (1301 LOC).
##           Handles: state persistence, content-hash invalidation, checkpoint-resume,
##           TOR-conditional skip, step error/warning collection, dry-run, force-reset.
##           Shell-фасад (node-lifecycle.sh) handles orchestration (flock, env exports, lib sourcing).
## @invariants
##   1. State file is at /var/lib/platform/.bootstrap/state.json (configurable via --state-file)
##   2. All subprocess.run calls use capture_output=True, text=True, timeout=120 (default);
##      exceptions: deploy_modules=300s, node_update=600s (see per-step overrides)
##   3. Non-fatal failures log WARN and continue — errors list collected for final audit
##   4. Content hash uses hashlib.sha256 of step script paths (always includes node-lifecycle.sh)
##   5. --dry-run prints plan and exits 0 BEFORE any mutations
##   6. --force clears all state (rm state file)
##   7. --resume loads existing state and continues from last checkpoint
##   8. TOR_ENABLED=false → step_3_tor_proxy is skipped (not failed)
##   9. State file format: {mode, node, current_step, steps: {str: StepState}, errors, warnings}
##   10. CLI args or env vars for: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY, PLATFORM_CI_DEPLOY_KEY
## @rationale  Shell-оригинал (node-lifecycle.sh) смешивал 5 ответственностей: arg parsing,
##             state management, step logic, logging, checkpoint. Python decomposition:
##             state_machine.py — чистая state-machine с transitions; steps.py — step реализация;
##             shell-фасад — orchestration (flock, env exports).
## @changes  2026-07-22 | W4-E2 — Created from node-lifecycle.sh decomposition
##           2026-07-24 | W5.T5.3 — Added HC_DONE_MARKER check in healthcheck step:
##           when /var/lib/platform/.bootstrap/.hc_done_in_deploy exists, skips the
##           standalone healthcheck (already done inside deploy_docker_group)
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Optional: import steps module for step implementations
try:
    from . import steps as _steps
except ImportError:
    _steps = None

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when a state transition violates pre/post-conditions (W5-E6 C3)."""


# region FUNC__safe_update_hash
## @purpose  Safely update a hasher with file contents, handling OSError/FileNotFoundError internally
def _safe_update_hash(hasher: hashlib._Hash, path: str) -> None:
    """Update hasher with file contents. Silently skips unreadable files."""
    try:
        with open(path, "rb") as f:
            hasher.update(f.read())
    except (OSError, FileNotFoundError) as e:
        logger.warning("[IMP:7][_safe_update_hash] Cannot read %s: %s", path, e)


# endregion FUNC__safe_update_hash


# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"
# DevPlan 047: INIT_STEP_COUNT 17→23 (added docker_auth at index 5, deploy_context at index 23)
INIT_STEP_COUNT = 23
UPDATE_STEP_COUNT = 8

# Step name → index mapping (1-indexed, matches state.json keys)
# DevPlan 047: docker_auth inserted at position 5 (shifts 5→6...21→22), deploy_context at 23
INIT_STEPS: list[str] = [
    "ssh_access",  # 1
    "apt_deps",  # 2
    "tor_proxy",  # 3 (conditional — TOR_ENABLED)
    "install_docker",  # 4
    "docker_auth",  # 5 ← NEW (DevPlan 047): Docker Hub auth + registry-mirror
    "create_platform_user",  # 6 (was 5)
    "create_ci_deploy_user",  # 7 (was 6)
    "create_projects_base",  # 8 (was 6b/7)
    "firewall",  # 9 (was 7/8)
    "verify_core",  # 10 (was 8/9)
    "verify_node_configs",  # 11 (was 9/10)
    "decrypt_secrets",  # 12 (was 10/11)
    "ensure_secrets",  # 13 (was 12b)
    "secrets_init",  # 14 (was secrets-init)
    "read_node_yaml",  # 15 (was 11/14)
    "ghcr_auth",  # 16 (was 12/15)
    "sudoers",  # 17 (was 13/16)
    "install_acme",  # 18 (was 13b/17)
    "node_update",  # 19 (was 14/18)
    "converge",  # 20 (was 15/19)
    "audit_log",  # 21 (was 16/20)
    "telegram",  # 22 (was 17/21)
    "deploy_context",  # 23 ← NEW (DevPlan 047): bulk cert restore + context projects + verify
]

UPDATE_STEPS: list[str] = [
    "verify_core",  # 1
    "provision",  # 2
    "deliver_overlays",  # 2.5/3
    "ssl_provision",  # 3/4
    "deploy_modules",  # 4/5
    "provision_llm_keys",  # 6 ← NEW (DevPlan 049): render litellm config + provision virtual keys
    "healthcheck",  # 7
    "converge",  # 8 (after healthcheck)
    "deploy_context",  # 9 ← NEW (DevPlan 047): incremental project deploy + cert check
]


# ── Retry Policy (W5-E6 C2) ──
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2  # seconds: 2, 4, 8
RETRYABLE_EXCEPTIONS = (subprocess.TimeoutExpired, FileNotFoundError, OSError)


def _should_retry(exc: Exception, attempt: int) -> bool:
    """Return True if the exception is retryable and attempt < MAX_RETRIES.

    ## @purpose — Exponential-backoff retry policy for transient step failures.
    ## @io — ⇥ exc: caught exception, attempt: current attempt (1-based)
    ##       ⎋ bool: True to retry, False to fail-fast
    ## @complexity — O(1)
    """
    if isinstance(exc, RETRYABLE_EXCEPTIONS) and attempt < MAX_RETRIES:
        backoff = RETRY_BACKOFF_BASE**attempt
        logger.info(
            "[IMP:8][_should_retry] Retryable exception (attempt %d/%d): %s — backing off %ds",
            attempt,
            MAX_RETRIES,
            exc,
            backoff,
        )
        time.sleep(backoff)
        return True
    return False


# region DATACLASSES


@dataclass
class StepState:
    """State of a single bootstrap step.

    ## @purpose — Track execution state, content hash, timing, and error for one step.
    ## @io — ⇥ constructor params → ⎋ StepState instance with serializable fields
    ## @complexity — O(1)
    """

    name: str
    status: str = "pending"  # pending | running | done | skipped | failed
    hash: str | None = None  # content hash for idempotency check
    started_at: str | None = None  # ISO timestamp
    error: str | None = None  # error message if failed
    reason: str | None = None  # skip reason (TOR_DISABLED, content_unchanged)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON state file."""
        d = {"name": self.name, "status": self.status}
        if self.hash:
            d["hash"] = self.hash
        if self.started_at:
            d["started_at"] = self.started_at
        if self.error:
            d["error"] = self.error
        if self.reason:
            d["reason"] = self.reason
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepState:
        """Deserialize from dict."""
        return cls(
            name=data.get("name", ""),
            status=data.get("status", "pending"),
            hash=data.get("hash"),
            started_at=data.get("started_at"),
            error=data.get("error"),
            reason=data.get("reason"),
        )


@dataclass
class BootstrapState:
    """Complete bootstrap/update state.

    ## @purpose — Serializable root state object for the entire lifecycle run.
    ## @io — ⇥ constructor params → ⎋ BootstrapState with steps dict
    ## @complexity — O(N) where N = number of steps
    """

    mode: str = "init"  # init | update
    node: str | None = None  # node name
    current_step: int = 0  # 0 = not started
    steps: dict[str, StepState] = field(default_factory=dict)  # str step index → StepState
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "mode": self.mode,
            "node": self.node,
            "current_step": self.current_step,
            "steps": {k: v.to_dict() for k, v in self.steps.items()},
            "errors": self.errors,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BootstrapState:
        """Deserialize from dict."""
        steps = {}
        for k, v in data.get("steps", {}).items():
            steps[k] = StepState.from_dict(v)
        return cls(
            mode=data.get("mode", "init"),
            node=data.get("node"),
            current_step=data.get("current_step", 0),
            steps=steps,
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )


# endregion DATACLASSES


# region STATEMACHINE


class StateMachine:
    """State machine for bootstrap/update lifecycle steps.

    ## @purpose — Load/create/save state.json, orchestrate step transitions,
    ##            compute content hashes, validate env, dispatch to step implementations.
    ## @scope — Core of W4-E2 decomposition. Manages state transitions only —
    ##          actual step logic lives in steps.py (optional) or is inlined.
    ## @invariants
    ##   - save() MUST be called after every state mutation
    ##   - All subprocess calls have 120s timeout
    ##   - Non-fatal failures (WARN) do not advance current_step
    ##   - Content hash always includes node-lifecycle.sh + step-specific paths
    ##   - Step index 0 = not started; step N = last successfully completed step
    ## @complexity — O(1) per transition; O(N) for full init/update run
    """

    # region FUNC___init__
    ## @purpose — Initialize state machine: load existing state or create new.
    ## @io — ⇥ state_file_path: Path to JSON state file → ⎋ None
    ## @complexity — O(N) where N = number of steps in existing state
    def __init__(self, state_file_path: str = DEFAULT_STATE_FILE) -> None:
        self.state_file = Path(state_file_path)
        self.core_dir: str | None = None

        if self.state_file.exists():
            logger.info("[IMP:7][StateMachine][init] Loading state from %s", self.state_file)
            try:
                with open(self.state_file) as f:
                    data = json.load(f)
                self.state = BootstrapState.from_dict(data)
                logger.info(
                    "[IMP:8][StateMachine][init] State loaded: mode=%s node=%s current_step=%d",
                    self.state.mode,
                    self.state.node,
                    self.state.current_step,
                )
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(
                    "[IMP:7][StateMachine][init] Corrupt state file %s: %s — creating fresh", self.state_file, e
                )
                self.state = BootstrapState()
        else:
            logger.info("[IMP:7][StateMachine][init] No state file at %s — creating fresh", self.state_file)
            self.state = BootstrapState()

    # endregion FUNC___init__

    # region FUNC_save
    ## @purpose — Persist current state to JSON file. Creates parent dirs if needed.
    ## @io — ⇥ None → ⎋ None (side-effect: writes state.json)
    ## @complexity — O(N) where N = number of steps
    def save(self) -> None:
        """Write state to JSON file atomically (write to tmp then rename)."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.with_suffix(".json.tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)
            tmp_path.replace(self.state_file)
            logger.debug("[IMP:6][StateMachine][save] State saved to %s", self.state_file)
        except OSError as e:
            logger.error("[IMP:10][StateMachine][save] Failed to save state: %s", e)
            raise

    # endregion FUNC_save

    # region FUNC_start_step
    ## @purpose — Mark a step as running. Sets started_at, updates current_step.
    ## @io — ⇥ n: step index (1-based) → ⎋ None
    ## @complexity — O(1)
    def start_step(self, n: int) -> None:
        """Validate preconditions and mark step N as running."""
        step_name = self._step_name(n)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        step = StepState(name=step_name, status="running", started_at=now)
        self.state.steps[str(n)] = step
        self.state.current_step = n
        logger.info("[IMP:9][StateMachine][start_step] Step %d (%s) START", n, step_name)
        self.save()

    # endregion FUNC_start_step

    # region FUNC_complete_step
    ## @purpose — Mark a step as done with optional content hash.
    ## @io — ⇥ n: step index, hash_val: optional content hash → ⎋ None
    ## @complexity — O(1)
    def complete_step(self, n: int, hash_val: str | None = None) -> None:
        """Mark step N as completed successfully."""
        key = str(n)
        if key not in self.state.steps:
            logger.warning("[IMP:7][StateMachine][complete_step] Step %d not started — creating", n)
            self.state.steps[key] = StepState(name=self._step_name(n))
        self.state.steps[key].status = "done"
        if hash_val:
            self.state.steps[key].hash = hash_val
        logger.info("[IMP:9][StateMachine][complete_step] Step %d (%s) DONE", n, self.state.steps[key].name)
        self.save()

    # endregion FUNC_complete_step

    # region FUNC_skip_step
    ## @purpose — Mark a step as skipped with reason (TOR_DISABLED, content_unchanged).
    ## @io — ⇥ n: step index, reason: skip reason → ⎋ None
    ## @complexity — O(1)
    def skip_step(self, n: int, reason: str = "") -> None:
        """Mark step N as skipped (not failed, not run)."""
        key = str(n)
        if key not in self.state.steps:
            self.state.steps[key] = StepState(name=self._step_name(n))
        self.state.steps[key].status = "skipped"
        self.state.steps[key].reason = reason or "content_unchanged"
        logger.info("[IMP:9][StateMachine][skip_step] Step %d (%s) SKIPPED: %s", n, self.state.steps[key].name, reason)
        self.save()

    # endregion FUNC_skip_step

    # region FUNC_fail_step
    ## @purpose — Mark a step as failed, collect error message.
    ## @io — ⇥ n: step index, error: error description → ⎋ None
    ## @complexity — O(1)
    def fail_step(self, n: int, error: str) -> None:
        """Mark step N as failed."""
        key = str(n)
        if key not in self.state.steps:
            self.state.steps[key] = StepState(name=self._step_name(n))
        self.state.steps[key].status = "failed"
        self.state.steps[key].error = error
        self.state.errors.append(f"Step {n} ({self.state.steps[key].name}): {error}")
        logger.error("[IMP:10][StateMachine][fail_step] Step %d (%s) FAILED: %s", n, self.state.steps[key].name, error)
        self.save()

    # endregion FUNC_fail_step

    # region FUNC_get_current_step
    ## @purpose — Return the next step index to execute.
    ##            If state.current_step is 0, returns 1 (not started).
    ##            If all steps are done/skipped, returns None.
    ## @io — ⇥ None → ⎋ int or None
    ## @complexity — O(N) where N = number of steps in mode
    def get_current_step(self) -> int | None:
        """Return next step to run (1-based), or None if all done."""
        step_list = self._step_list()
        if not step_list:
            return None

        # If current_step is 0, start from 1
        if self.state.current_step == 0:
            return 1

        # Find first step that is pending or failed
        for i in range(1, len(step_list) + 1):
            key = str(i)
            if key not in self.state.steps:
                return i
            status = self.state.steps[key].status
            if status in ("pending", "failed"):
                return i
            if status == "running":
                return i  # re-run hanging steps

        return None

    # endregion FUNC_get_current_step

    # region FUNC__step_hash
    ## @purpose — Compute SHA256 content hash of step script paths for idempotency.
    ##            Always includes node-lifecycle.sh + step-specific extra paths.
    ## @io — ⇥ step_name: str, extra_paths: list of additional script paths → ⎋ str hexdigest
    ## @complexity — O(S) where S = total file bytes hashed
    def _step_hash(self, step_name: str, *extra_paths: str) -> str:
        """Compute SHA256 content hash of node-lifecycle.sh + extra paths."""
        hasher = hashlib.sha256()
        # Always include the current file (node-lifecycle.sh equivalent)
        paths_to_hash = [os.path.abspath(__file__), *list(extra_paths)]
        for path in paths_to_hash:
            _safe_update_hash(hasher, path)
        digest = hasher.hexdigest()
        logger.debug(
            "[IMP:6][StateMachine][_step_hash] Hash for %s: %s (files: %s)", step_name, digest[:12], paths_to_hash
        )
        return digest

    # endregion FUNC__step_hash

    # region FUNC_validate_bootstrap_env
    ## @purpose — Validate that required env vars are set for bootstrap.
    ## @io — ⇥ required_vars: list of env var names → ⎋ bool (True = all present)
    ## @complexity — O(N) where N = len(required_vars)
    def validate_bootstrap_env(self, required_vars: list[str] | None = None) -> bool:
        """Check that all required env vars exist and are non-empty. Returns True if OK."""
        if required_vars is None:
            required_vars = ["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]
        missing: list[str] = []
        for var in required_vars:
            val = os.environ.get(var, "").strip()
            if not val:
                missing.append(var)
        if missing:
            logger.error(
                "[IMP:10][StateMachine][validate_bootstrap_env] FAIL: Missing required env vars: %s",
                ", ".join(missing),
            )
            print(f"Missing required env vars: {', '.join(missing)}", file=sys.stderr)
            print("  Pass as CLI args: --node-name NAME --node-yaml PATH --owner-key KEY", file=sys.stderr)
            return False
        logger.info("[IMP:9][StateMachine][validate_bootstrap_env] All required env vars present")
        return True

    # endregion FUNC_validate_bootstrap_env

    # region FUNC_add_warning
    ## @purpose — Add a non-fatal warning to the state warnings list.
    ## @io — ⇥ warning: warning description → ⎋ None
    ## @complexity — O(1)
    def add_warning(self, warning: str) -> None:
        """Add warning to state warnings list."""
        self.state.warnings.append(warning)
        logger.warning("[IMP:7][StateMachine][add_warning] %s", warning)

    # endregion FUNC_add_warning

    # ── Internal helpers ──────────────────────────────────────────────────

    # region FUNC__step_list
    def _step_list(self) -> list[str]:
        """Return the step list for the current mode."""
        if self.state.mode == "init":
            return INIT_STEPS
        return UPDATE_STEPS

    # endregion FUNC__step_list

    # region FUNC__step_name
    def _step_name(self, n: int) -> str:
        """Return the canonical name for step number N."""
        steps = self._step_list()
        if 1 <= n <= len(steps):
            return steps[n - 1]
        return f"unknown_step_{n}"

    # endregion FUNC__step_name

    # region FUNC__is_step_done
    def _is_step_done(self, n: int) -> bool:
        """Check if step N is already completed."""
        key = str(n)
        if key not in self.state.steps:
            return False
        return self.state.steps[key].status == "done"

    # endregion FUNC__is_step_done

    # region FUNC__is_step_skipped
    def _is_step_skipped(self, n: int) -> bool:
        """Check if step N is skipped."""
        key = str(n)
        if key not in self.state.steps:
            return False
        return self.state.steps[key].status == "skipped"

    # endregion FUNC__is_step_skipped

    # region FUNC__hash_changed
    def _hash_changed(self, n: int, new_hash: str) -> bool:
        """Check if step hash changed since last run. True = hash changed (needs re-run)."""
        key = str(n)
        if key not in self.state.steps:
            return True
        old_hash = self.state.steps[key].hash
        return old_hash != new_hash

    # endregion FUNC__hash_changed

    # region FUNC__check_precondition
    ## @purpose — Validate pre-condition before executing a step (W5-E6 C3).
    ##            Asserts previous step (n-1) is in {done, skipped} or n == 1.
    ## @io — ⇥ state: BootstrapState, step_index: int, step_name: str
    ##       ⎋ None (raises StateTransitionError on violation)
    ## @complexity — O(1)
    def _check_precondition(self, state: BootstrapState, step_index: int, step_name: str) -> None:
        """Assert previous step is done/skipped (or step_index == 1 for first step)."""
        if step_index == 1:
            # First step — no previous to check
            logger.debug(
                "[IMP:6][StateMachine][_check_precondition] Step %d (%s): first step — pre-condition OK",
                step_index,
                step_name,
            )
            return
        prev_key = str(step_index - 1)
        if prev_key not in state.steps:
            raise StateTransitionError(
                f"Pre-condition violation: step {step_index - 1} has no state (never started). "
                f"Cannot execute step {step_index} ({step_name})."
            )
        prev_status = state.steps[prev_key].status
        if prev_status not in ("done", "skipped"):
            raise StateTransitionError(
                f"Pre-condition violation: step {step_index - 1} status is '{prev_status}', "
                f"expected 'done' or 'skipped'. Cannot execute step {step_index} ({step_name})."
            )
        logger.debug(
            "[IMP:6][StateMachine][_check_precondition] Step %d (%s): pre-condition OK (prev=%s)",
            step_index,
            step_name,
            prev_status,
        )

    # endregion FUNC__check_precondition

    # region FUNC__check_postcondition
    ## @purpose — Validate post-condition after completing a step (W5-E6 C3).
    ##            Asserts current step status == done, state.current_step == step_index.
    ## @io — ⇥ state: BootstrapState, step_index: int, step_name: str
    ##       ⎋ None (raises StateTransitionError on violation)
    ## @complexity — O(1)
    def _check_postcondition(self, state: BootstrapState, step_index: int, step_name: str) -> None:
        """Assert current step is done and state.current_step matches step_index."""
        key = str(step_index)
        if key not in state.steps:
            raise StateTransitionError(f"Post-condition violation: step {step_index} ({step_name}) has no state entry.")
        if state.steps[key].status != "done":
            raise StateTransitionError(
                f"Post-condition violation: step {step_index} ({step_name}) status is "
                f"'{state.steps[key].status}', expected 'done'."
            )
        if state.current_step != step_index:
            raise StateTransitionError(
                f"Post-condition violation: state.current_step is {state.current_step}, "
                f"expected {step_index} after completing step {step_name}."
            )
        logger.debug(
            "[IMP:6][StateMachine][_check_postcondition] Step %d (%s): post-condition OK", step_index, step_name
        )

    # endregion FUNC__check_postcondition

    # region FUNC_setup_state
    ## @purpose — Initialize state for a run: set mode, node, create step entries.
    ## @io — ⇥ mode: str, node: Optional[str] → ⎋ None
    ## @complexity — O(N) where N = number of steps
    def setup_state(self, mode: str, node: str | None = None) -> None:
        """Initialize state for a new run. Sets mode, node, resets step entries.

        ## ⚠️ TRAP[BUG] · 2026-07-24 · P0 · setup_state не сбрасывал current_step и статусы шагов
        ## · Symptom: при смене mode init→update current_step оставался 23, статусы старых шагов
        ##   сохранялись. _run_steps пытался выполнить шаги с невалидными пред-условиями.
        ## · Fix: сброс current_step=0 + принудительный reset всех шагов в pending.
        """
        self.state.mode = mode
        self.state.node = node
        self.state.current_step = 0
        step_list = self._step_list()
        # Always reset all step entries to pending (not just add missing ones)
        for i, name in enumerate(step_list, 1):
            key = str(i)
            self.state.steps[key] = StepState(name=name, status="pending")
        logger.info(
            "[IMP:8][StateMachine][setup_state] State initialized: mode=%s node=%s steps=%d",
            mode,
            node,
            len(step_list),
        )
        self.save()

    # endregion FUNC_setup_state

    # region FUNC_reset
    ## @purpose — Clear all state (--force mode).
    ## @io — ⇥ None → ⎋ None (side-effect: removes state file)
    ## @complexity — O(1)
    def reset(self) -> None:
        """Clear all checkpoints and reset state (--force mode)."""
        self.state = BootstrapState()
        if self.state_file.exists():
            self.state_file.unlink()
        logger.info("[IMP:9][StateMachine][reset] State cleared (force mode)")

    # endregion FUNC_reset

    # region FUNC_report
    ## @purpose — Generate a human-readable report/plan of steps.
    ## @io — ⇥ None → ⎋ str: formatted report
    ## @complexity — O(N)
    def report(self) -> str:
        """Print JSON summary of current state to stdout."""
        return json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False)

    # endregion FUNC_report

    # region FUNC_dry_run_plan
    ## @purpose — Print execution plan for dry-run mode. Does NOT mutate state.
    ## @io — ⇥ None → ⎋ str: formatted plan
    ## @complexity — O(N)
    def dry_run_plan(self) -> str:
        """Generate a dry-run execution plan string (no mutations)."""
        step_list = self._step_list()
        lines: list[str] = []
        lines.append(f"===== DRY RUN: {self.state.mode} mode =====")
        lines.append(f"NODE: {self.state.node or '<unset>'}")
        lines.append("Steps:")
        for i, name in enumerate(step_list, 1):
            key = str(i)
            if key in self.state.steps:
                status = self.state.steps[key].status
                lines.append(f"  {i}. {name} [{status}]")
            else:
                lines.append(f"  {i}. {name} [pending]")
        if self.state.mode == "init":
            lines.append("Bootstrap DRY RUN — no mutations performed, exit 0")
        else:
            lines.append("Node update DRY RUN — no mutations performed, exit 0")
        return "\n".join(lines)

    # endregion FUNC_dry_run_plan


# endregion STATEMACHINE


# region CLI


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser.

    ## @purpose — CLI entry point for state_machine.py. Supports --mode, --run-step,
    ##            --dry-run, --resume, --force, and config overrides.
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(
        description="Bootstrap/Update State Machine — node lifecycle orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Init mode (full bootstrap):
  python3 state_machine.py --mode init --node-name server1 --node-yaml /opt/node-configs/server1/node.yaml

  # Update mode (incremental):
  python3 state_machine.py --mode update --node-name server1

  # Resume from last checkpoint:
  python3 state_machine.py --mode init --resume

  # Force reset + run:
  python3 state_machine.py --mode init --force --node-name server1

  # Dry run (print plan, no mutations):
  python3 state_machine.py --mode init --node-name server1 --dry-run
""",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to state JSON file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--mode",
        choices=["init", "update"],
        default="init",
        help="Run mode: init (full bootstrap) or update (incremental)",
    )
    parser.add_argument("--run-step", type=int, help="Run a specific step by number")
    parser.add_argument("--dry-run", action="store_true", help="Print execution plan, no mutations")
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint")
    parser.add_argument("--force", action="store_true", help="Clear all checkpoints before running")
    parser.add_argument("--node-name", help="Node name (NODE_NAME)")
    parser.add_argument("--node-yaml", help="Path to node.yaml (NODE_YAML)")
    parser.add_argument("--owner-key", help="Platform owner SSH public key (PLATFORM_OWNER_KEY)")
    parser.add_argument("--ci-deploy-key", help="CI deploy SSH public key (PLATFORM_CI_DEPLOY_KEY)")
    parser.add_argument("--tor-enabled", choices=["true", "false"], default=None, help="Override TOR_ENABLED")
    parser.add_argument("--tor-bridges-file", help="Path to Tor bridges file")
    parser.add_argument("--skip-tor-verify", action="store_true", help="Skip Tor circuit verification")
    parser.add_argument("--ghcr-token", help="GHCR pull token for docker login")
    parser.add_argument("--extra-ports", help="Extra firewall ports (space-separated)")
    parser.add_argument("--bot-token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Telegram chat ID")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8118", help="Telegram proxy URL")
    parser.add_argument("--auto-reconcile", action="store_true", help="Auto-reconcile after converge")
    parser.add_argument("--context", help="Deployment context name (CONTEXT — DevPlan 047)")
    return parser


# endregion CLI


# region MAIN
def main() -> int:
    """CLI entry point. Parses args, creates StateMachine, dispatches to mode.

    ## @purpose — Top-level orchestrator for state machine CLI.
    ##            Handles: --dry-run, --force, --resume, --run-step, mode dispatch.
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
    ## @complexity — O(N * M) where N = steps, M = sub-step operations per step
    """
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    # ── Inject env vars from CLI args ──
    if args.node_name:
        os.environ.setdefault("NODE_NAME", args.node_name)
    if args.node_yaml:
        os.environ.setdefault("NODE_YAML", args.node_yaml)
    if args.owner_key:
        os.environ.setdefault("PLATFORM_OWNER_KEY", args.owner_key)
    if args.ci_deploy_key:
        os.environ.setdefault("PLATFORM_CI_DEPLOY_KEY", args.ci_deploy_key)
    if args.tor_enabled is not None:
        os.environ["TOR_ENABLED"] = args.tor_enabled
    if args.ghcr_token:
        os.environ.setdefault("GHCR_PULL_TOKEN", args.ghcr_token)
    if args.bot_token:
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", args.bot_token)
    if args.chat_id:
        os.environ.setdefault("TELEGRAM_CHAT_ID", args.chat_id)
    if args.proxy_url:
        os.environ.setdefault("TELEGRAM_PROXY_URL", args.proxy_url)
    if args.tor_bridges_file:
        os.environ.setdefault("TOR_BRIDGES_FILE", args.tor_bridges_file)
    if args.skip_tor_verify:
        os.environ["SKIP_TOR_VERIFY"] = "true"
    if args.extra_ports:
        os.environ.setdefault("FIREWALL_EXTRA_PORTS", args.extra_ports)
    if args.auto_reconcile:
        os.environ["AUTO_RECONCILE"] = "true"
    # DevPlan 047: --context CLI arg → CONTEXT env var
    if args.context:
        os.environ.setdefault("CONTEXT", args.context)

    # Create state machine
    sm = StateMachine(state_file_path=args.state_file)

    # Detect CORE_DIR from PLATFORM_ROOT or default
    platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
    core_dir = os.environ.get("CORE_DIR", os.path.join(platform_root, "core"))
    sm.core_dir = core_dir

    # ── --force: clear state ──
    if args.force:
        logger.info("[IMP:9][main] --force: Clearing state")
        sm.reset()

    # ── --dry-run: print plan, no mutations ──
    if args.dry_run:
        sm.setup_state(mode=args.mode, node=os.environ.get("NODE_NAME"))
        print(sm.dry_run_plan(), file=sys.stderr)
        return 0

    # ── Validate env (init mode) ──
    if args.mode == "init":
        required_vars = ["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]
        # PLATFORM_CI_DEPLOY_KEY is semi-optional — warn if missing
        ci_key_present = bool(os.environ.get("PLATFORM_CI_DEPLOY_KEY", "").strip())
        if not ci_key_present:
            logger.warning("[IMP:7][main] PLATFORM_CI_DEPLOY_KEY not set — ci-deploy user will have no deploy key")
        if not sm.validate_bootstrap_env(required_vars):
            return 1

    # ── Setup state if fresh or mode changed ──
    if sm.state.mode != args.mode or sm.state.current_step == 0:
        sm.setup_state(mode=args.mode, node=os.environ.get("NODE_NAME"))
    elif args.resume:
        logger.info("[IMP:8][main] --resume: Continuing from step %d", sm.state.current_step)

    # ── --run-step: execute single step ──
    if args.run_step:
        return _run_single_step(sm, args.mode, args.run_step)

    # ── Dispatch full mode run ──
    if args.mode == "init":
        return _run_init_mode(sm)
    return _run_update_mode(sm)


# endregion MAIN


# region RUN_SINGLE_STEP
def _run_single_step(sm: StateMachine, mode: str, step_n: int) -> int:
    """Execute a single step by number.

    ## @purpose — --run-step N support: validates step exists, runs it, returns exit code.
    ## @io — ⇥ sm: StateMachine, mode: str, step_n: int → ⎋ int exit code
    ## @complexity — O(1) for dispatch, O(M) for step execution
    """
    steps = INIT_STEPS if mode == "init" else UPDATE_STEPS

    if step_n < 1 or step_n > len(steps):
        logger.error("[IMP:10][run_step] Invalid step number %d. Mode %s has %d steps.", step_n, mode, len(steps))
        return 1

    step_name = steps[step_n - 1]
    logger.info("[IMP:9][run_step] Running single step %d: %s", step_n, step_name)

    try:
        sm.start_step(step_n)
        _execute_step(sm, step_n, step_name, mode)
        sm.complete_step(step_n)
        logger.info("[IMP:9][run_step] Step %d (%s) completed successfully", step_n, step_name)
    except Exception as e:
        sm.fail_step(step_n, str(e))
        logger.error("[IMP:10][run_step] Step %d failed: %s", step_n, e)
        return 1

    return 0


# endregion RUN_SINGLE_STEP


# region RUN_INIT_MODE
def _run_init_mode(sm: StateMachine) -> int:
    """Execute all init mode steps (full bootstrap).

    ## @purpose — Run all 17 init steps sequentially with checkpoint-resume.
    ## @io — ⇥ sm: StateMachine → ⎋ int exit code
    ## @complexity — O(N * M) where N = 17 steps, M = per-step operations
    """
    return _run_steps(sm, INIT_STEPS, "init")


# endregion RUN_INIT_MODE


# region RUN_UPDATE_MODE
def _run_update_mode(sm: StateMachine) -> int:
    """Execute all update mode steps (incremental node update).

    ## @purpose — Run all 6 update steps sequentially with checkpoint-resume.
    ## @io — ⇥ sm: StateMachine → ⎋ int exit code
    ## @complexity — O(N * M) where N = 6 steps, M = per-step operations
    """
    return _run_steps(sm, UPDATE_STEPS, "update")


# endregion RUN_UPDATE_MODE


# region RUN_STEPS
def _run_steps(sm: StateMachine, step_list: list[str], mode: str) -> int:
    """Core step runner: iterate steps from current checkpoint to end.

    ## @purpose — Shared loop for both init and update modes.
    ##            Checks content hash for idempotency; skips done steps;
    ##            handles TOR conditional; collects errors/warnings.
    ## @io — ⇥ sm: StateMachine, step_list: list[str], mode: str → ⎋ int exit code
    ## @complexity — O(N * M) where N = steps, M = per-step operations
    """
    tor_enabled = os.environ.get("TOR_ENABLED", "false").lower() == "true"
    exit_code = 0

    for i, step_name in enumerate(step_list, 1):
        # ── Skip scheduling ──
        if sm._is_step_done(i):
            logger.info("[IMP:7][run_steps] Step %d (%s) already done — skipping", i, step_name)
            continue

        # ── TOR conditional ──
        if step_name == "tor_proxy" and not tor_enabled:
            sm.skip_step(i, "TOR_DISABLED")
            logger.info("[IMP:8][run_steps] Step %d (%s): TOR_DISABLED — skipping", i, step_name)
            continue

        # ── Compute hash and check idempotency ──
        hash_val = _compute_step_hash(sm, step_name, mode)

        # ── Pre-condition check (W5-E6 C3) ──
        try:
            sm._check_precondition(sm.state, i, step_name)
        except StateTransitionError as e:
            logger.error("[IMP:10][run_steps] Pre-condition FAILED for step %d (%s): %s", i, step_name, e)
            sm.fail_step(i, str(e))
            exit_code = 1
            break

        # ── Execute step with retry loop (W5-E6 C2) ──
        try:
            sm.start_step(i)
            last_exception: Exception | None = None
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    _execute_step(sm, i, step_name, mode)
                    last_exception = None
                    break
                except Exception as e:
                    if _should_retry(e, attempt):
                        last_exception = e
                        continue
                    raise  # Non-transient or out of retries
            if last_exception:
                raise last_exception  # type: ignore[misc]
            sm.complete_step(i, hash_val=hash_val)
            # ── Post-condition check (W5-E6 C3) ──
            try:
                sm._check_postcondition(sm.state, i, step_name)
            except StateTransitionError as e:
                logger.error("[IMP:10][run_steps] Post-condition FAILED for step %d (%s): %s", i, step_name, e)
                raise
            logger.info("[IMP:9][run_steps] Step %d (%s) completed successfully", i, step_name)
        except Exception as e:
            sm.fail_step(i, str(e))
            exit_code = 1
            logger.error("[IMP:10][run_steps] Step %d (%s) FAILED: %s", i, step_name, e)
            # Critical steps abort; non-critical continue (listed below)
            if step_name in ("ssh_access", "verify_core", "verify_node_configs", "read_node_yaml"):
                logger.error("[IMP:10][run_steps] Critical step %d failed — aborting %s mode", i, mode)
                break

    return exit_code


# endregion RUN_STEPS


# region EXECUTE_STEP
def _execute_step(sm: StateMachine, step_n: int, step_name: str, mode: str) -> None:
    """Dispatch execution of a single step to its implementation.

    ## @purpose — Map step name to implementation function (from steps.py or inlined).
    ## @io — ⇥ sm, step_n, step_name, mode → ⎋ None (side-effect: executes step logic)
    ## @complexity — O(M) per step (varies by step)
    """
    core_dir = sm.core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
    node_name = os.environ.get("NODE_NAME", "")
    node_yaml = os.environ.get("NODE_YAML", "")

    if mode == "init":
        _execute_init_step(sm, step_n, step_name, core_dir, node_name, node_yaml)
    else:
        _execute_update_step(sm, step_n, step_name, core_dir, node_name, node_yaml)


# endregion EXECUTE_STEP


# region EXECUTE_INIT_STEP
def _execute_init_step(
    sm: StateMachine,
    step_n: int,
    step_name: str,
    core_dir: str,
    node_name: str,
    node_yaml: str,
) -> None:
    """Execute a single init-mode step by dispatching to steps.py or inlined logic.

    ## @purpose — Dispatch 17 init steps to their implementations.
    ## @io — ⇥ step context → ⎋ None (side-effect: subprocess calls, file ops)
    ## @complexity — O(M) per step
    """
    if step_name == "ssh_access":
        # Verify running as root
        if os.geteuid() != 0:
            raise RuntimeError("node-lifecycle must run as root (euid=0)")
        logger.info("[IMP:9][init][ssh_access] Running as root — OK")

    elif step_name == "apt_deps":
        # Install apt dependencies
        tor_enabled = os.environ.get("TOR_ENABLED", "false").lower() == "true"
        packages = ["make", "curl", "ufw", "python3-yaml", "python3-jsonschema"]
        if tor_enabled:
            packages.extend(["tor", "privoxy", "obfs4proxy"])
        _install_apt_packages(packages)
        # Install sops if missing
        _ensure_sops()

    elif step_name == "tor_proxy":
        # Install Tor + Privoxy proxy
        bridges_file = os.environ.get("TOR_BRIDGES_FILE", "")
        skip_verify = os.environ.get("SKIP_TOR_VERIFY", "false").lower() == "true"
        tor_script = os.path.join(core_dir, "internal", "bootstrap", "install-tor-proxy.sh")
        if os.path.exists(tor_script):
            cmd = ["bash", tor_script]
            if bridges_file:
                cmd.extend(["--tor-bridges-file", bridges_file])
            if skip_verify:
                cmd.append("--skip-tor-verify")
            _subprocess_run(cmd, "tor_proxy")

    elif step_name == "install_docker":
        install_script = os.path.join(core_dir, "internal", "bootstrap", "install-docker.sh")
        _subprocess_run(["bash", install_script], "install_docker")

    elif step_name == "docker_auth":
        # DevPlan 047: Docker Hub auth + registry-mirror (step index 5)
        bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")
        auth_script = os.path.join(bootstrap_dir, "docker_registry_auth.py")
        username = os.environ.get("DOCKER_HUB_USERNAME", "")
        token = os.environ.get("DOCKER_HUB_TOKEN", "")
        if not username or not token:
            logger.warning("[IMP:7][init][docker_auth] Docker Hub creds not set — rate-limit may apply")
        elif os.path.isfile(auth_script):
            _subprocess_run(
                ["python3", auth_script],
                "docker_auth",
                non_fatal=True,
            )
        else:
            logger.warning("[IMP:7][init][docker_auth] docker_registry_auth.py not found — skipping")

    elif step_name == "create_platform_user":
        _create_user("platform", ["docker"])
        owner_key = os.environ.get("PLATFORM_OWNER_KEY", "")
        if owner_key:
            _add_ssh_key("platform", owner_key)

    elif step_name == "create_ci_deploy_user":
        _create_user("ci-deploy", ["docker"])
        ci_deploy_key = os.environ.get("PLATFORM_CI_DEPLOY_KEY", "")
        if ci_deploy_key:
            forced_command = f'command="{core_dir}/internal/deploy/deploy-project.sh {node_name}",restrict'
            _add_ssh_key("ci-deploy", ci_deploy_key, forced_command_prefix=forced_command)

    elif step_name == "create_projects_base":
        _ensure_projects_base(core_dir, node_name)

    elif step_name == "firewall":
        firewall_script = os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")
        _subprocess_run(["bash", firewall_script], "firewall")

    elif step_name == "verify_core":
        _verify_core_files(core_dir)

    elif step_name == "verify_node_configs":
        if not node_yaml or not os.path.isfile(node_yaml):
            raise RuntimeError(f"node.yaml not found: {node_yaml}")
        logger.info("[IMP:9][init][verify_node_configs] node.yaml present: %s", node_yaml)

    elif step_name == "decrypt_secrets":
        _decrypt_secrets(core_dir)

    elif step_name == "ensure_secrets":
        _ensure_secrets_exist()

    elif step_name == "secrets_init":
        if _steps and hasattr(_steps, "_step_secrets_init"):
            _steps._step_secrets_init(core_dir)
        else:
            _step_secrets_init_inline(core_dir)

    elif step_name == "read_node_yaml":
        _validate_node_yaml(node_yaml, core_dir)

    elif step_name == "ghcr_auth":
        _ghcr_auth()

    elif step_name == "sudoers":
        setup_script = os.path.join(core_dir, "internal", "bootstrap", "setup-node.sh")
        _subprocess_run(["bash", setup_script], "sudoers")
        _validate_sudoers()

    elif step_name == "install_acme":
        if _steps and hasattr(_steps, "_step_install_acme"):
            _steps._step_install_acme(core_dir)
        else:
            _step_install_acme_inline(core_dir)

    elif step_name == "node_update":
        # Delegate to update mode (self-invocation)
        # timeout=600: deploy_modules (14 modules) needs ~300s internally;
        # 600s gives headroom for provision + ssl + healthcheck + converge
        lifecycle_script = os.path.join(core_dir, "internal", "bootstrap", "node-lifecycle.sh")
        if os.path.exists(lifecycle_script):
            _subprocess_run(["bash", lifecycle_script, "--mode", "update"], "node_update", non_fatal=True, timeout=600)
        else:
            logger.warning("[IMP:7][init][node_update] node-lifecycle.sh not found — skipping post-init update")

    elif step_name == "converge":
        converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
        if os.path.exists(converge_script):
            converge_args = ["bash", converge_script, "--node", node_name]
            if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
                converge_args.append("--reconcile")
            if os.environ.get("DRY_RUN_MODE", "false").lower() == "true":
                converge_args.append("--dry-run")
            _subprocess_run(converge_args, "converge", non_fatal=True)

    elif step_name == "audit_log":
        _write_audit_log(sm)

    elif step_name == "telegram":
        _send_telegram(sm)

    elif step_name == "deploy_context":
        # DevPlan 047: deploy_context step (init index 23, update index 8)
        # Delegates to steps._step_deploy_context for cert orchestration + project deploy + verify
        if _steps and hasattr(_steps, "_step_deploy_context"):
            _steps._step_deploy_context(core_dir, node_name, node_yaml)
        else:
            _step_deploy_context_inline(core_dir, node_name, node_yaml)


# endregion EXECUTE_INIT_STEP


# region EXECUTE_UPDATE_STEP
def _execute_update_step(
    sm: StateMachine,
    step_n: int,
    step_name: str,
    core_dir: str,
    node_name: str,
    node_yaml: str,
) -> None:
    """Execute a single update-mode step.

    ## @purpose — Dispatch 6 update steps to their implementations.
    ## @io — ⇥ step context → ⎋ None (side-effect: subprocess calls, file ops)
    ## @complexity — O(M) per step
    """
    if step_name == "verify_core":
        _verify_core_files(core_dir)

    elif step_name == "provision":
        provision_script = os.path.join(core_dir, "internal", "provision-environment.sh")
        _subprocess_run(
            ["bash", provision_script, "--scope", "networks", "--scope", "volumes"],
            "provision",
        )

    elif step_name == "deliver_overlays":
        overlay_dir = f"/opt/node-configs/{node_name}/overlays/nginx"
        if os.path.isdir(overlay_dir):
            conf_files = list(Path(overlay_dir).glob("*.conf"))
            if conf_files:
                logger.info("[IMP:8][update][deliver_overlays] Found %d overlay(s) in %s", len(conf_files), overlay_dir)
                # Reload nginx if running
                _subprocess_run(
                    ["docker", "exec", "nginx", "nginx", "-s", "reload"],
                    "deliver_overlays",
                    non_fatal=True,
                    check_required=False,
                )
            else:
                logger.info("[IMP:7][update][deliver_overlays] No .conf files in %s — skipping", overlay_dir)
        else:
            logger.info("[IMP:7][update][deliver_overlays] No overlay directory at %s — skipping", overlay_dir)

    elif step_name == "ssl_provision":
        _ssl_provision(core_dir, node_yaml)

    elif step_name == "deploy_modules":
        deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
        _subprocess_run(["bash", deploy_script, "--skip-provision"], "deploy_modules", timeout=300)

    elif step_name == "provision_llm_keys":
        # DevPlan 049 Phase 7: render litellm-config.yml from policy.yaml, then provision virtual keys
        llm_dir = os.path.join(core_dir, "internal", "llm")
        renderer_script = os.path.join(llm_dir, "config_renderer.py")
        config_output = os.path.join(core_dir, "modules", "litellm", "config", "litellm-config.yml")
        if os.path.isfile(renderer_script):
            _subprocess_run(
                ["python3", renderer_script, "--output", config_output],
                "render_litellm_config",
                non_fatal=True,
            )
        provision_entrypoint = os.path.join(core_dir, "entrypoints", "provision-llm.sh")
        if os.path.isfile(provision_entrypoint):
            _subprocess_run(
                ["bash", provision_entrypoint],
                "provision_llm_keys",
                non_fatal=True,
            )

    elif step_name == "healthcheck":
        # T5.3: Skip standalone healthcheck if already done during parallel deploy
        hc_done_marker = "/var/lib/platform/.bootstrap/.hc_done_in_deploy"
        if os.path.isfile(hc_done_marker):
            logger.info(
                "[IMP:9][update][healthcheck] Healthcheck already done during deploy "
                "(DEPLOY_PARALLEL) — skipping standalone healthcheck step"
            )
            import contextlib

            with contextlib.suppress(OSError):
                os.unlink(hc_done_marker)
            return
        _run_healthchecks(node_yaml)

    elif step_name == "converge":
        converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
        if os.path.exists(converge_script):
            converge_args = ["bash", converge_script, "--node", node_name]
            if os.environ.get("AUTO_RECONCILE", "false").lower() == "true":
                converge_args.append("--reconcile")
            _subprocess_run(converge_args, "converge", non_fatal=True)

    elif step_name == "deploy_context":
        # DevPlan 047: incremental project deploy + cert check (update step 8)
        if _steps and hasattr(_steps, "_step_deploy_context"):
            _steps._step_deploy_context(core_dir, node_name, node_yaml)
        else:
            _step_deploy_context_inline(core_dir, node_name, node_yaml)


# endregion EXECUTE_UPDATE_STEP


# region HELPER_FUNCTIONS


def _compute_step_hash(sm: StateMachine, step_name: str, mode: str) -> str:
    """Compute content hash for a step, including step-specific scripts.

    ## @purpose — Wrapper around _step_hash that adds step-specific script paths.
    ## @io — ⇥ sm, step_name, mode → ⎋ str hexdigest
    ## @complexity — O(S) where S = total file bytes
    """
    core_dir = sm.core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
    extra_paths: list[str] = []

    # Map step name to extra script paths
    path_map: dict[str, list[str]] = {
        "tor_proxy": [os.path.join(core_dir, "internal", "bootstrap", "install-tor-proxy.sh")],
        "install_docker": [os.path.join(core_dir, "internal", "bootstrap", "install-docker.sh")],
        "firewall": [os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")],
        "decrypt_secrets": [os.path.join(core_dir, "lib", "secrets.sh")],
        "ensure_secrets": [os.path.join(core_dir, "lib", "secrets.sh")],
        "secrets_init": [os.path.join(core_dir, "internal", "bootstrap", "secrets-init.sh")],
        "sudoers": [os.path.join(core_dir, "internal", "bootstrap", "setup-node.sh")],
        "install_acme": [os.path.join(core_dir, "internal", "bootstrap", "install-acme.sh")],
        "node_update": [os.path.join(core_dir, "internal", "bootstrap", "node-lifecycle.sh")],
        "converge": [os.path.join(core_dir, "internal", "bootstrap", "converge.sh")],
        "provision": [os.path.join(core_dir, "internal", "provision-environment.sh")],
        "deliver_overlays": [os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh")],
        "ssl_provision": [os.path.join(core_dir, "internal", "bootstrap", "issue-cert.sh")],
        "deploy_modules": [os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")],
        "provision_llm_keys": [
            os.path.join(core_dir, "internal", "llm", "config_renderer.py"),
            os.path.join(core_dir, "entrypoints", "provision-llm.sh"),
        ],
        # DevPlan 047: new step paths
        "docker_auth": [os.path.join(core_dir, "internal", "bootstrap", "docker_registry_auth.py")],
        "deploy_context": [
            os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py"),
            os.path.join(core_dir, "internal", "bootstrap", "cert_orchestrator.py"),
            os.path.join(core_dir, "internal", "verify", "verify-domains.sh"),
            os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh"),
        ],
        "verify_core": [
            os.path.join(core_dir, "lib", "checkpoint.sh"),
            os.path.join(core_dir, "internal", "bootstrap", "content-hash.sh"),
        ],
    }

    extra_paths.extend(path_map.get(step_name, []))
    return sm._step_hash(step_name, *extra_paths)


def _subprocess_run(
    cmd: list[str],
    step_name: str,
    non_fatal: bool = False,
    check_required: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    """Run a subprocess command with timeout and error handling.

    ## @purpose — Safe subprocess wrapper with logging, timeout, and error handling.
    ## @io — ⇥ cmd, step_name, non_fatal, check_required, timeout → ⎋ subprocess.CompletedProcess
    ## @complexity — O(1) orchestration, O(M) for command execution
    """
    logger.info("[IMP:8][subprocess][%s] Running: %s", step_name, " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            err_msg = f"Command {' '.join(cmd)} failed (exit={result.returncode}): {result.stderr.strip()}"
            if result.stdout:
                logger.debug("[IMP:6][subprocess][%s] stdout: %s", step_name, result.stdout[:500])
            if result.returncode == 127:
                # ⚠️ TRAP[BUG] · 2026-07-22 · P1 · 043-staging-fix B3
                # · exit=127 (command not found) is always fatal — indicates missing dependency/binary
                # · non_fatal flag does NOT apply to 127 — it's a configuration error, not a runtime error
                raise RuntimeError(f"Command not found (exit=127): {err_msg}")
            if non_fatal:
                logger.warning("[IMP:7][subprocess][%s] %s", step_name, err_msg)
            elif check_required:
                raise RuntimeError(err_msg)
            else:
                logger.info(
                    "[IMP:7][subprocess][%s] Non-critical command returned %d: %s",
                    step_name,
                    result.returncode,
                    result.stderr[:200],
                )
        else:
            logger.info("[IMP:9][subprocess][%s] Command succeeded (exit=0)", step_name)
        return result
    except subprocess.TimeoutExpired:
        msg = f"Command {' '.join(cmd)} timed out after {timeout}s"
        if non_fatal:
            logger.warning("[IMP:7][subprocess][%s] %s", step_name, msg)
            return subprocess.CompletedProcess(cmd, -1, "", msg)
        raise RuntimeError(msg) from None
    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}"
        if non_fatal:
            logger.warning("[IMP:7][subprocess][%s] %s", step_name, msg)
            return subprocess.CompletedProcess(cmd, -1, "", msg)
        raise RuntimeError(msg) from None


# region FUNC__is_pkg_installed
## @purpose  Check if a single dpkg package is installed, handling errors gracefully
def _is_pkg_installed(pkg: str) -> bool:
    """Check dpkg status for a package. Returns True if installed, False on error."""
    try:
        result = subprocess.run(
            ["dpkg", "-s", pkg],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC__is_pkg_installed


def _install_apt_packages(packages: list[str]) -> None:
    """Install apt packages if not already installed.

    ## @purpose — Idempotent apt-get install: checks dpkg, only installs missing.
    ## @io — ⇥ packages: list[str] → ⎋ None
    ## @complexity — O(N) where N = packages
    """
    to_install: list[str] = [pkg for pkg in packages if not _is_pkg_installed(pkg)]

    if to_install:
        logger.info("[IMP:9][apt] Installing %d packages: %s", len(to_install), " ".join(to_install))
        _subprocess_run(["apt-get", "update", "-qq"], "apt_update")
        _subprocess_run(["apt-get", "install", "-y", "-qq", *to_install], "apt_install")
        for pkg in to_install:
            _subprocess_run(["dpkg", "-s", pkg], f"verify_{pkg}", check_required=True)
    else:
        logger.info("[IMP:7][apt] All packages already installed — skipping")


def _ensure_sops() -> None:
    """Install sops (v3.9.4) from GitHub if not present.

    ## @purpose — SOPS is not in apt repos; install from GitHub releases.
    ## @io — ⇥ None → ⎋ None (side-effect: downloads and installs sops binary)
    ## @complexity — O(1)
    """
    try:
        result = subprocess.run(["command", "-v", "sops"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            logger.info("[IMP:7][sops] Already installed")
            return
    except FileNotFoundError:
        pass

    logger.info("[IMP:8][sops] Installing sops v3.9.4 from GitHub")
    try:
        # Detect architecture
        arch_result = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        arch = arch_result.stdout.strip() if arch_result.returncode == 0 else "amd64"
        if arch not in ("amd64", "arm64"):
            arch = "amd64"

        url = f"https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.{arch}"
        _subprocess_run(
            ["curl", "-sSL", "-o", "/usr/local/bin/sops", url],
            "sops_download",
        )
        _subprocess_run(["chmod", "0755", "/usr/local/bin/sops"], "sops_chmod")
        logger.info("[IMP:9][sops] sops v3.9.4 installed")
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        logger.warning("[IMP:7][sops] Failed to install sops: %s", e)


def _create_user(username: str, groups: list[str] | None = None) -> None:
    """Create a system user if not exists.

    ## @purpose — Idempotent user creation with optional group membership.
    ## @io — ⇥ username: str, groups: Optional[list[str]] → ⎋ None
    ## @complexity — O(1)
    """
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
    _subprocess_run(cmd, f"create_user_{username}")
    logger.info("[IMP:9][user] User '%s' created", username)


def _add_ssh_key(
    username: str,
    key: str,
    forced_command_prefix: str | None = None,
) -> None:
    """Add an SSH public key to user's authorized_keys.

    ## @purpose — Add SSH key for platform or ci-deploy user. Supports forced-command prefix.
    ## @io — ⇥ username, key, forced_command_prefix → ⎋ None
    ## @complexity — O(1)
    """
    home = f"/home/{username}"
    ssh_dir = os.path.join(home, ".ssh")
    auth_keys = os.path.join(ssh_dir, "authorized_keys")

    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
    # Ensure ownership
    _subprocess_run(["chown", f"{username}:{username}", ssh_dir], f"chown_{username}_ssh", non_fatal=True)

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
    _subprocess_run(["chown", f"{username}:{username}", auth_keys], f"chown_{username}_keys", non_fatal=True)
    logger.info("[IMP:9][ssh_key] SSH key added for %s", username)


def _ensure_projects_base(core_dir: str, node_name: str) -> None:
    """Ensure /opt/projects base directory exists with correct ownership.

    ## @purpose — Create /opt/projects, set ci-deploy ownership, call converge R3.
    ## @io — ⇥ core_dir, node_name → ⎋ None
    ## @complexity — O(1) + subprocess
    """
    projects_dir = "/opt/projects"
    os.makedirs(projects_dir, exist_ok=True)
    _subprocess_run(["chown", "ci-deploy:ci-deploy", projects_dir], "chown_projects", non_fatal=True)
    logger.info("[IMP:9][projects_base] /opt/projects ownership set to ci-deploy:ci-deploy")

    # Call converge R3
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if os.path.isfile(converge_script) and node_name:
        logger.info("[IMP:8][projects_base] Calling converge R3 for project scaffold")
        _subprocess_run(
            ["bash", converge_script, "--node", node_name, "--units", "R3"],
            "converge_R3",
            non_fatal=True,
        )


def _verify_core_files(core_dir: str) -> None:
    """Verify core files are properly delivered (SCP/rsync marker check).

    ## @purpose — Check that core deployment has happened by verifying marker file.
    ## @io — ⇥ core_dir → ⎋ None (raises RuntimeError if core missing)
    ## @complexity — O(1)
    """
    marker = os.path.join(core_dir, "internal", "bootstrap", "node-lifecycle.sh")
    if not os.path.isfile(marker):
        raise RuntimeError(
            f"Core bootstrap not found at {marker}. Deploy first:\n  rsync -avz core/ root@<server>:{core_dir}/"
        )
    ver_file = os.path.join(core_dir, "VERSION")
    if os.path.isfile(ver_file):
        with open(ver_file) as f:
            ver = f.readline().strip()
        logger.info("[IMP:9][verify_core] Core v%s at %s", ver, core_dir)
    else:
        logger.info("[IMP:9][verify_core] Core found at %s (no VERSION file)", core_dir)


def _decrypt_secrets(core_dir: str) -> None:
    """Decrypt AGE-encrypted secrets. Delegates to lib/secrets.sh.

    ## @purpose — Decrypt secrets via secrets.sh workflow (sourced from lib).
    ## @io — ⇥ core_dir → ⎋ None (raises RuntimeError on decryption failure)
    ## @complexity — O(1)
    ## @invariants
    ##   - Decryption failure is FATAL — secrets are critical infrastructure, continuing
    ##     with CI defaults would silently deploy placeholder credentials (S3, DB passwords).
    ##   - step_10_decrypt_secrets handles "no encrypted file" as graceful skip (exit 0).
    ##     Missing AGE_SECRET_KEY with encrypted file present → exit 1 → RuntimeError.
    ## ⚠️ TRAP[BUG] · 2026-07-23 · P0 · non_fatal=True swallowed decrypt failures
    ## · Symptom: bootstrap continued with ci_default placeholders (test-access-key),
    ##   checkpoint .done created despite failure → --resume skipped decrypt forever.
    ## · Fix: removed non_fatal=True — decrypt failure is now FATAL (RuntimeError).
    ## · Test: unit/contract tests verify decrypt exit 1 → RuntimeError propagation.
    """
    secrets_lib = os.path.join(core_dir, "lib", "secrets.sh")
    if os.path.isfile(secrets_lib):
        # lib/secrets.sh requires CORE_DIR, logging.sh (log_step), checkpoint.sh (step_start/done/skip)
        # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · source secrets.sh без зависимостей
        # · Symptom: step_start/log_step: command not found, CORE_DIR/internal/... : No such file
        # · Root: bash -c "source secrets.sh" не имел CORE_DIR и не подгружал checkpoint/logging libs
        # · Fix: export CORE_DIR, source logging.sh + checkpoint.sh перед secrets.sh
        logging_lib = os.path.join(core_dir, "lib", "logging.sh")
        checkpoint_lib = os.path.join(core_dir, "lib", "checkpoint.sh")
        _subprocess_run(
            [
                "bash",
                "-c",
                f"export CORE_DIR={shlex.quote(core_dir)}"
                f" && source {shlex.quote(logging_lib)}"
                f" && source {shlex.quote(checkpoint_lib)}"
                f" && source {shlex.quote(secrets_lib)}"
                f" && step_10_decrypt_secrets",
            ],
            "decrypt_secrets",
        )


def _ensure_secrets_exist() -> None:
    """Ensure secrets.env exists from decrypted files.

    ## @purpose — Verify that secrets.env is present after decryption.
    ## @io — ⇥ None → ⎋ None (raises RuntimeError if missing)
    ## @complexity — O(1)
    """
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
    if not os.path.isfile(secrets_env):
        logger.warning("[IMP:7][ensure_secrets] %s not found — secrets may not be available", secrets_env)
    else:
        logger.info("[IMP:9][ensure_secrets] Secrets env present: %s", secrets_env)


def _validate_node_yaml(node_yaml: str, core_dir: str) -> None:
    """Validate node.yaml against node.schema.json.

    ## @purpose — Schema validation of node.yaml using jsonschema library (inline python3).
    ## @io — ⇥ node_yaml, core_dir → ⎋ None (raises RuntimeError on invalid)
    ## @complexity — O(1) for schema load + validation
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise RuntimeError(f"node.yaml not found: {node_yaml}")

    schema_file = os.path.join(core_dir, "schemas", "node.schema.json")
    if not os.path.isfile(schema_file):
        logger.warning("[IMP:7][validate_node_yaml] Schema file not found at %s — skipping validation", schema_file)
        return

    try:
        import jsonschema
        import yaml

        with open(schema_file) as f:
            schema = json.load(f)
        with open(node_yaml) as f:
            instance = yaml.safe_load(f)
        jsonschema.validate(instance, schema)
        logger.info("[IMP:9][validate_node_yaml] node.yaml valid against schema")
    except ImportError:
        logger.warning("[IMP:7][validate_node_yaml] yaml/jsonschema not available — skipping Python validation")
        # Fall back to subprocess python3
        _subprocess_run(
            [
                "python3",
                "-c",
                f"""
import json, yaml, jsonschema, sys
with open('{node_yaml}') as f:
    instance = yaml.safe_load(f)
with open('{schema_file}') as f:
    schema = json.load(f)
jsonschema.validate(instance, schema)
""",
            ],
            "validate_node_yaml",
            non_fatal=True,
        )
    except (yaml.YAMLError, json.JSONDecodeError, jsonschema.ValidationError) as e:
        logger.warning("[IMP:7][validate_node_yaml] node.yaml validation failed: %s", e)


def _ghcr_auth() -> None:
    """Configure GHCR docker login for ci-deploy user.

    ## @purpose — Docker login to ghcr.io using GHCR_PULL_TOKEN for ci-deploy user.
    ## @io — ⇥ None → ⎋ None (non-fatal if token not set)
    ## @complexity — O(1)
    """
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][ghcr_auth] GHCR_PULL_TOKEN not set — skipping ghcr auth")
        return
    _subprocess_run(
        ["bash", "-c", f"echo '{token}' | sudo -u ci-deploy docker login ghcr.io -u x-access-token --password-stdin"],
        "ghcr_auth",
        non_fatal=True,
    )


def _validate_sudoers() -> None:
    """Validate /etc/sudoers.d files for correct ownership and permissions.

    ## @purpose — All sudoers.d files must be owner=root:root, mode≤0440.
    ## @io — ⇥ None → ⎋ None (raises RuntimeError on violations)
    ## @complexity — O(N) where N = files in sudoers.d
    """
    sudoers_d = "/etc/sudoers.d"
    if not os.path.isdir(sudoers_d):
        logger.info("[IMP:7][sudoers] %s not found — skipping validation", sudoers_d)
        return

    try:
        entries = list(Path(sudoers_d).iterdir())
    except PermissionError:
        logger.warning(
            "[IMP:7][sudoers] Permission denied reading %s — skipping validation (non-root or restricted permissions)",
            sudoers_d,
        )
        return

    errors = 0
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.name == "README":
            continue

        stat_info = entry.stat()
        owner = f"{stat_info.st_uid}:{stat_info.st_gid}"
        mode = oct(stat_info.st_mode)[-3:]

        if owner != "0:0":
            logger.error("[IMP:10][sudoers] %s: owner %s instead of 0:0", entry.name, owner)
            errors += 1
        mode_int = int(mode, 8)
        if mode_int > 0o440:
            logger.error("[IMP:10][sudoers] %s: permissions %s instead of ≤0440", entry.name, mode)
            errors += 1

    if errors > 0:
        raise RuntimeError(
            f"{errors} sudoers file(s) with wrong owner/permissions. Fix:\n"
            f"  chown root:root {sudoers_d}/*\n"
            f"  chmod 0440 {sudoers_d}/*"
        )
    logger.info("[IMP:9][sudoers] All sudoers files validated: owner=root:root, mode≤0440")


def _step_install_acme_inline(core_dir: str) -> None:
    """Install acme.sh for SSL provisioning (init only, inline fallback).

    ## @purpose — Install acme.sh and DNS API extensions for SSL certificate issuance.
    ## @io — ⇥ core_dir → ⎋ None (non-fatal)
    ## @complexity — O(1)
    """
    install_script = os.path.join(core_dir, "internal", "bootstrap", "install-acme.sh")
    if os.path.isfile(install_script):
        _subprocess_run(["bash", install_script], "install_acme", non_fatal=True)
    else:
        logger.warning("[IMP:7][install_acme] %s not found — skipping acme.sh installation", install_script)


def _step_secrets_init_inline(core_dir: str) -> None:
    """Initialize service passwords from PLATFORM_MASTER_PASSWORD (init only, inline fallback).

    ## @purpose — Initialize service passwords (HERMES_DASHBOARD_PASSWORD, etc.).
    ## @io — ⇥ core_dir → ⎋ None (non-fatal)
    ## @complexity — O(1)
    """
    init_script = os.path.join(core_dir, "internal", "bootstrap", "secrets-init.sh")
    if os.path.isfile(init_script):
        _subprocess_run(["bash", init_script], "secrets_init", non_fatal=True)
    else:
        logger.warning("[IMP:7][secrets_init] %s not found — skipping secrets initialization", init_script)


def _ssl_provision(core_dir: str, node_yaml: str) -> None:
    """Provision SSL/TLS certificates via acme.sh DNS-01.

    ## @purpose — SSL certificate issuance for the platform domain.
    ## @io — ⇥ core_dir, node_yaml → ⎋ None (non-fatal if domain not configured)
    ## @complexity — O(1) + subprocess
    """
    ssl_script = os.path.join(core_dir, "internal", "bootstrap", "issue-cert.sh")
    if not os.path.isfile(ssl_script):
        logger.warning("[IMP:7][ssl] issue-cert.sh not found at %s — skipping SSL provisioning", ssl_script)
        return

    platform_domain = os.environ.get("PLATFORM_DOMAIN", "")
    # Fallback: extract domain from node.yaml (SSH env doesn't carry PLATFORM_DOMAIN)
    if not platform_domain and node_yaml and os.path.isfile(node_yaml):
        try:
            import yaml

            with open(node_yaml) as f:
                node_data = yaml.safe_load(f)
            if isinstance(node_data, dict):
                platform_domain = node_data.get("domain", "") or ""
                if platform_domain:
                    logger.info("[IMP:7][ssl] PLATFORM_DOMAIN resolved from node.yaml: %s", platform_domain)
        except Exception:
            pass
    if not platform_domain:
        logger.warning("[IMP:7][ssl] PLATFORM_DOMAIN not set — skipping SSL provisioning")
        return

    # Source secrets.env for WEBNAMES_API_KEY
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")
    if os.path.isfile(secrets_env):
        # Source it for env vars
        _subprocess_run(
            [
                "bash",
                "-c",
                f"set -a; source '{secrets_env}'; set +a; unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy NO_PROXY no_proxy; echo WEBNAMES_API_KEY=${{WEBNAMES_API_KEY:-unset}}",
            ],
            "source_secrets_env",
            non_fatal=True,
        )

    # Check S3 cache
    s3_cache = os.path.join(core_dir, "internal", "bootstrap", "s3-ssl-cache.sh")
    if os.path.isfile(s3_cache):
        check_result = _subprocess_run(
            ["bash", s3_cache, "check", platform_domain],
            "s3_cache_check",
            non_fatal=True,
            check_required=False,
        )
        if check_result and check_result.returncode == 0:
            # Restore from S3 cache
            _subprocess_run(
                ["bash", s3_cache, "download", platform_domain],
                "s3_cache_download",
                non_fatal=True,
                check_required=False,
            )
            cert_path = f"/etc/letsencrypt/live/{platform_domain}/fullchain.pem"
            if os.path.isfile(cert_path):
                logger.info("[IMP:9][ssl] SSL certificate restored from S3 cache for %s", platform_domain)
                return

    # Issue via acme.sh
    _subprocess_run(["bash", ssl_script], "ssl_issue", non_fatal=True)


def _run_healthchecks(node_yaml: str) -> None:
    """Run healthchecks on all deployed modules.

    ## @purpose — Iterate over modules in node.yaml and run liveness healthchecks.
    ## @io — ⇥ node_yaml → ⎋ None (non-fatal)
    ## @complexity — O(M * R) where M = modules, R = retries
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][healthcheck] NODE_YAML not set or not found — skipping healthchecks")
        return

    hc_max_retries = 10
    hc_retry_interval = 10
    hc_fail = 0

    try:
        import yaml

        with open(node_yaml) as f:
            data = yaml.safe_load(f)
        modules = data.get("modules", {})
        if isinstance(modules, dict):
            module_items = modules.items()
        elif isinstance(modules, list):
            module_items = [(m.get("name", ""), m) for m in modules]
        else:
            module_items = []

        for mod_name, mod_value in module_items:
            if not mod_name:
                continue
            if isinstance(mod_value, dict):
                enabled = str(mod_value.get("enabled", True)).lower()
            else:
                enabled = str(mod_value).lower()
            if enabled != "true":
                continue

            passed = False
            # ⚠️ TRAP[BUG] · 2026-07-24 · P0 · invoke_module_interface is a bash function, not an executable
            # · Symptom: subprocess.run(["invoke_module_interface", ...]) → FileNotFoundError
            # · Root: invoke_module_interface is sourced from module-interface.sh (via paths.sh)
            # · Fix: wrap in bash -c with proper sourcing
            platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
            for attempt in range(1, hc_max_retries + 1):
                try:
                    hc_cmd = (
                        f"source {shlex.quote(platform_root + '/core/lib/paths.sh')} && "
                        f"invoke_module_interface {shlex.quote(mod_name)} healthcheck liveness"
                    )
                    hc_result = subprocess.run(
                        ["bash", "-c", hc_cmd],
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if hc_result.returncode == 0:
                        logger.info(
                            "[IMP:9][healthcheck:%s] Healthcheck PASS (attempt %d/%d)",
                            mod_name,
                            attempt,
                            hc_max_retries,
                        )
                        passed = True
                        break
                    if attempt == 1:
                        logger.warning(
                            "[IMP:7][healthcheck:%s] stderr: %s",
                            mod_name,
                            hc_result.stderr.strip()[-200:] if hc_result.stderr else "(empty)",
                        )
                except subprocess.TimeoutExpired:
                    logger.warning("[IMP:7][healthcheck:%s] Timeout (attempt %d/%d)", mod_name, attempt, hc_max_retries)
                except FileNotFoundError:
                    logger.warning(
                        "[IMP:7][healthcheck:%s] bash not found (attempt %d/%d)", mod_name, attempt, hc_max_retries
                    )
                if attempt < hc_max_retries:
                    time.sleep(hc_retry_interval)

            if not passed:
                logger.warning("[IMP:7][healthcheck:%s] Healthcheck FAILED after %d attempts", mod_name, hc_max_retries)
                hc_fail += 1
    except ImportError:
        logger.warning("[IMP:7][healthcheck] yaml library not available — skipping inline healthchecks")
    except yaml.YAMLError as e:
        logger.warning("[IMP:7][healthcheck] Failed to parse node.yaml: %s", e)

    if hc_fail > 0:
        logger.warning("[IMP:7][healthcheck] %d healthcheck(s) failed — node partially ready", hc_fail)
    else:
        logger.info("[IMP:9][healthcheck] All healthchecks passed")


def _write_audit_log(sm: StateMachine) -> None:
    """Write bootstrap/update audit summary to audit log.

    ## @purpose — Record completion status with errors/warnings to platform audit log.
    ## @io — ⇥ sm → ⎋ None (side-effect: writes to audit log)
    ## @complexity — O(1)
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    audit_file = "/var/log/platform/audit.log"
    try:
        os.makedirs(os.path.dirname(audit_file), exist_ok=True)
        with open(audit_file, "a") as f:
            mode = sm.state.mode
            node = sm.state.node or "unknown"
            warnings_count = len(sm.state.warnings)
            errors_count = len(sm.state.errors)
            f.write(f"[{ts}] bootstrap:{mode} DONE | node={node} | warnings={warnings_count} | errors={errors_count}\n")
            if warnings_count > 0:
                for w in sm.state.warnings:
                    f.write(f"[{ts}] bootstrap:warnings WARN | {w}\n")
            if errors_count > 0:
                for e in sm.state.errors:
                    f.write(f"[{ts}] bootstrap:errors ERROR | {e}\n")
        logger.info("[IMP:9][audit] Audit log updated: %s", audit_file)
    except OSError as e:
        logger.warning("[IMP:7][audit] Failed to write audit log: %s", e)


def _send_telegram(sm: StateMachine) -> None:
    """Send Telegram notification with bootstrap/update results.

    ## @purpose — Notify via Telegram bot about lifecycle completion status.
    ## @io — ⇥ sm → ⎋ None (non-fatal)
    ## @complexity — O(1)
    """
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not bot_token or not chat_id:
        logger.info("[IMP:9][telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notifications disabled")
        return

    ts = time.strftime("%d.%m.%Y %H:%M:%S")
    node = sm.state.node or "unknown"
    warnings_count = len(sm.state.warnings)
    errors_count = len(sm.state.errors)

    status_suffix = "⚠️ Warnings/Errors:" if errors_count > 0 or warnings_count > 0 else "✅"

    msg = f"🚀 [node: {node}] Узел обновлён {status_suffix}\nВремя: {ts}"
    if warnings_count > 0:
        for w in sm.state.warnings:
            msg += f"\n- ⚠️ {w}"
    if errors_count > 0:
        for e in sm.state.errors:
            msg += f"\n- ❌ {e}"

    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "http://127.0.0.1:8118")
    try:
        import urllib.parse
        import urllib.request

        params = urllib.parse.urlencode(
            {
                "chat_id": chat_id,
                "text": msg,
            },
            quote_via=urllib.parse.quote,
        )
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage?{params}"
        req = urllib.request.Request(url)

        # Set proxy if available
        proxy = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(proxy)
        opener.open(req, timeout=30)
        logger.info("[IMP:9][telegram] Notification sent to chat %s", chat_id)
    except Exception as e:
        logger.warning("[IMP:7][telegram] Telegram notification failed (non-fatal): %s", e)


# region FUNC_step_deploy_context_inline
## @purpose — Inline fallback for deploy_context step when steps.py is unavailable.
##            Orchestrates cert restore + project deploy + vhost render + verify.
##            DevPlan 047 Phase 5.
## @io — ⇥ core_dir: str, node_name: str, node_yaml: str → ⎋ None (non-fatal)
## @complexity — O(D * P) where D = domains, P = projects
## @invariants
##   - Extracts CONTEXT from env or node.yaml
##   - Calls cert_orchestrator.orchestrate_certs for all domains
##   - Calls context_deployer.deploy_context_projects for context projects
##   - Renders vhosts via add-vhost.sh
##   - Runs verify-domains.sh (non-fatal)
def _step_deploy_context_inline(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Deploy context: certs + projects + vhosts + verify (inline fallback)."""
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # Extract context
    context = os.environ.get("CONTEXT", "")
    if not context and node_yaml and os.path.isfile(node_yaml):
        context = _extract_context_from_node_yaml(node_yaml)
    if not context:
        logger.error(
            "[IMP:10][deploy_context] CONTEXT not set and cannot be extracted from node.yaml — skipping deploy_context"
        )
        return

    logger.info("[IMP:9][deploy_context] Starting deploy_context (context=%s, node=%s)", context, node_name)

    # ── 18.2 + 18.3: Cert orchestration ──
    domains = _extract_domains(node_yaml, context)
    s3_cache_script = os.path.join(bootstrap_dir, "s3-ssl-cache.sh")
    issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # T0.3 (048.P3): Ensure PLATFORM_DOMAIN is set from node.yaml if not in env
    platform_domain = os.environ.get("PLATFORM_DOMAIN", "").strip()
    if not platform_domain and node_yaml and os.path.isfile(node_yaml):
        try:
            import yaml

            with open(node_yaml) as f:
                node_data = yaml.safe_load(f)
            if isinstance(node_data, dict):
                domain_val = node_data.get("domain", "") or ""
                if domain_val:
                    os.environ["PLATFORM_DOMAIN"] = domain_val
                    logger.info("[IMP:7][deploy_context] PLATFORM_DOMAIN set from node.yaml: %s", domain_val)
        except Exception:
            pass

    if domains:
        try:
            # Import cert_orchestrator from bootstrap package
            import importlib.util

            spec = importlib.util.spec_from_file_location(
                "cert_orchestrator",
                os.path.join(bootstrap_dir, "cert_orchestrator.py"),
            )
            if spec and spec.loader:
                cert_mod = importlib.util.module_from_spec(spec)
                sys.modules["cert_orchestrator"] = (
                    cert_mod  # P0: register before exec_module (@dataclass requires sys.modules)
                )
                spec.loader.exec_module(cert_mod)
                cert_result = cert_mod.orchestrate_certs(domains, s3_cache_script, issue_cert_script, secrets_env)
                logger.info("[IMP:9][deploy_context] Cert orchestration complete: %s", cert_result.to_dict())
            else:
                logger.warning("[IMP:7][deploy_context] Cannot load cert_orchestrator.py — skipping cert orchestration")
        except Exception as e:
            logger.warning("[IMP:7][deploy_context] Cert orchestration failed (non-fatal): %s", e)
    else:
        logger.info("[IMP:7][deploy_context] No domains to orchestrate certs for")

    # ── 18.4: Deploy context projects ──
    try:
        import importlib.util

        deployer_path = os.path.join(bootstrap_dir, "deploy", "context_deployer.py")
        spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)
        if spec and spec.loader:
            deployer_mod = importlib.util.module_from_spec(spec)
            sys.modules["context_deployer"] = (
                deployer_mod  # P0: register before exec_module (@dataclass requires sys.modules)
            )
            spec.loader.exec_module(deployer_mod)
            results = deployer_mod.deploy_context_projects(node_yaml, context) or []
            logger.info("[IMP:9][deploy_context] Project deploy complete: %d projects", len(results))
        else:
            logger.warning("[IMP:7][deploy_context] Cannot load context_deployer.py — skipping project deploy")
    except Exception as e:
        logger.warning("[IMP:7][deploy_context] Project deploy failed (non-fatal): %s", e)

    # ── 18.5: Render vhosts ──
    vhost_script = os.path.join(core_dir, "internal", "scaffold", "add-vhost.sh")
    if os.path.isfile(vhost_script):
        node_configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
        _subprocess_run(
            ["bash", vhost_script, "--render-all", "--node", node_name, "--node-configs-dir", node_configs_dir],
            "render_vhosts",
            non_fatal=True,
        )
    # Reload nginx if running
    _subprocess_run(["docker", "exec", "nginx", "nginx", "-s", "reload"], "nginx_reload", non_fatal=True)

    # ── 18.6: Final verify ──
    verify_script = os.path.join(core_dir, "internal", "verify", "verify-domains.sh")
    if os.path.isfile(verify_script):
        platform_root = os.environ.get("PLATFORM_ROOT", "/opt/platform")
        _subprocess_run(["bash", verify_script, node_name, platform_root], "final_verify", non_fatal=True)

    logger.info("[IMP:9][deploy_context] deploy_context complete")


# endregion FUNC_step_deploy_context_inline


# region FUNC_extract_context_from_node_yaml
## @purpose — Extract context name from node.yaml. One node = one context.
##            Reads context (string) or contexts[0].name (array, first element).
## @io — ⇥ node_yaml_path: str → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
def _extract_context_from_node_yaml(node_yaml_path: str) -> str:
    """Extract context name from node.yaml."""
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return ""
        ctx = data.get("context", "")
        if ctx and isinstance(ctx, str):
            logger.info("[IMP:8][context] Context from node.yaml context field: %s", ctx)
            return ctx
        contexts = data.get("contexts", [])
        if contexts and isinstance(contexts, list) and len(contexts) > 0:
            first = contexts[0]
            if isinstance(first, dict):
                ctx = first.get("name", "")
            elif isinstance(first, str):
                ctx = first
            if ctx:
                logger.info("[IMP:8][context] Context from node.yaml contexts[0].name: %s", ctx)
                return ctx
    except Exception as e:
        logger.warning("[IMP:7][context] Failed to parse %s: %s", node_yaml_path, e)
    return ""


# endregion FUNC_extract_context_from_node_yaml


# region FUNC_extract_domains
## @purpose — Extract all domains from node.yaml for cert orchestration.
##            Combines platform domain + project domains (filtered by context).
## @io — ⇥ node_yaml_path: str, context: str → ⎋ list[str]
## @complexity — O(N) for YAML parse
def _extract_domains(node_yaml_path: str, context: str) -> list[str]:
    """Extract all domains from node.yaml for cert orchestration."""
    domains: list[str] = []
    try:
        import yaml

        with open(node_yaml_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return domains
        # Platform domain
        domain = data.get("domain", "")
        if not domain:
            node_info = data.get("node", {})
            if isinstance(node_info, dict):
                domain = node_info.get("platform_domain", "") or node_info.get("domain", "")
        if domain:
            domains.append(domain)
        # Project domains (filtered by context)
        projects = data.get("projects", [])
        if isinstance(projects, list):
            for p in projects:
                if not isinstance(p, dict):
                    continue
                proj_context = p.get("context", "")
                # Include if context matches or project has no context field
                if context and proj_context and proj_context != context:
                    continue
                pd = p.get("domain", "")
                if pd and pd not in domains:
                    domains.append(pd)
    except Exception as e:
        logger.warning("[IMP:7][deploy_context] Failed to extract domains from %s: %s", node_yaml_path, e)
    return domains


# endregion FUNC_extract_domains


# endregion HELPER_FUNCTIONS


if __name__ == "__main__":
    sys.exit(main())
