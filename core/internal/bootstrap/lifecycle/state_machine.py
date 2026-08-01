#!/usr/bin/env python3
# GREP_SUMMARY: state-machine, bootstrap, lifecycle, node-init, node-update, checkpoint-resume, step-transitions, state-json, content-hash, BootstrapPhase, phase-dependency-graph, precondition-check
# STRUCTURE: ▶ [BootstrapPhase enum (14)] → ┌StepState + BootstrapState + _phase_dependency_graph┐ → ◇ precondition_check() → ○ _execute_phase() / _execute_grouped_phase() → ⚡ save() → ⎋ CLI dispatch (init/update)
# region MODULE_CONTRACT
## @purpose  Explicit state machine for node-lifecycle.sh bootstrap/update process.
##           Manages 14 consolidated phases (φ1-φ13 + φ8.5) via a JSON state file
##           at /var/lib/platform/.bootstrap/state.json (configurable).
##           Each phase is a typed transition with precondition and dependency checks.
## @scope    Python-side of W4-E2 Strangler-Fig decomposition of node-lifecycle.sh (1301 LOC).
##           Handles: state persistence, content-hash invalidation, checkpoint-resume,
##           phase precondition checks, phase dependency graph, grouped-phase sub-checkpoints,
##           TOR-conditional skip, error/warning collection,
##           dry-run, force-reset. Business logic extraction → phases.py.
## @invariants
##   1. State file is at /var/lib/platform/.bootstrap/state.json (configurable via --state-file)
##   2. All subprocess.run calls use capture_output=True, text=True, timeout=120;
##      exception: node_update=600s (self-invocation wraps entire update pipeline:
##      deploy 14 modules ~300s + provision + ssl + healthcheck + converge)
##   3. Non-fatal failures log WARN and continue — errors list collected for final audit
##   4. Content hash uses hashlib.sha256 of step script paths (always includes node-lifecycle.sh)
##   5. --dry-run prints plan and exits 0 BEFORE any mutations
##   6. --force clears all state (rm state file)
##   7. --resume loads existing state and continues from last checkpoint
##   8. TOR_ENABLED=false → tor_proxy sub-step is skipped (not failed)
##   9. State file format: {mode, node, current_step, phases: {str: PhaseState}, errors, warnings}
##   10. CLI args or env vars for: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY, PLATFORM_CI_DEPLOY_KEY
##   11. Phase dependency graph enforces execution order: φ2 ← φ1, φ4 ← φ3, φ6 ← φ4, φ8 ← φ4+φ6+φ7
##   12. precondition_check() verifies intra-phase conditions BEFORE execution
##   13. grouped-phases (φ1-φ5, φ7, φ12) support sub_checkpoints for granular skip
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph
##             and precondition checks. Eliminates 8 silent failure propagation points.
##             _phase_dependency_graph replaces implicit sequential ordering with explicit DAG.
## @changes  2026-07-22 | W4-E2 — Created from node-lifecycle.sh decomposition
##           2026-07-24 | W5.T5.3 — Added HC_DONE_MARKER check in healthcheck step
##           2026-07-25 | DevPlan 071 Rev 2 — Name-based state.json keys, numeric-key backward compat
##           2026-07-30 | T19/T20a/T21 — Shared module extraction (telegram_notifier, docker_auth)
##           2026-07-30 | DevPlan 087 — BootstrapPhase enum (14 values), _phase_dependency_graph,
##           precondition_check(), _execute_phase(), _execute_grouped_phase().
##           Added `--phase` CLI argument for phase-level execution.
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)


class StateTransitionError(Exception):
    """Raised when a state transition violates pre/post-conditions (W5-E6 C3)."""


class PhaseDependencyError(Exception):
    """Raised when a phase's dependency graph check fails — a prerequisite phase is not done.

    ## @purpose — Distinguish structural phase ordering violations from intra-phase precondition failures.
    ##             Operator sees: "Phase φ6 requires φ4, but φ4 is pending".
    """


class PhasePreconditionError(Exception):
    """Raised when a phase's precondition_check() fails — intra-phase condition not met.

    ## @purpose — Intra-phase condition (root access, file exists, network available).
    ##             Operator sees: "Phase φ1 precondition failed: must run as root (euid=0)".
    """


# ── BootstrapPhase enum (14 consolidated phases per DevPlan 087) ──
class BootstrapPhase:
    """Consolidated bootstrap phases — 14 values (9 init + 5 update).

    φ1-φ8.5 = INIT mode phases
    φ9-φ13  = UPDATE mode phases

    ## @purpose — Canonical phase names replacing 23+ old step names.
    ##             Each phase maps to a function in phases.py.
    ## @invariants
    ##   - 14 values total (len(list(BootstrapPhase.init_phases()) + list(update_phases())) == 14)
    ##   - Value string is the canonical state.json key for the phase
    ##   - converge_services (φ8.5) and converge_update (φ13) are separate for clear init/update separation
    """

    # ── INIT mode phases (φ1-φ8.5) ──
    SYSTEM_BOOTSTRAP = "system_bootstrap"  # φ1: packages, docker, tor, firewall
    USER_ACCOUNTS = "user_accounts"  # φ2: users, SSH keys, projects base
    PLATFORM_SETUP = "platform_setup"  # φ3: docker auth, metrics cron
    SECRETS_PROVISION = "secrets_provision"  # φ4: decrypt, ensure-passwords
    NODE_CONFIGURATION = "node_configuration"  # φ5: node.yaml, verify core, configs
    REGISTRY_AUTH = "registry_auth"  # φ6: ghcr auth, docker auth
    CERTIFICATES = "certificates"  # φ7: acme.sh, ssl provision
    DEPLOY_SERVICES = "deploy_services"  # φ8: deploy modules, deploy context
    CONVERGE_SERVICES = "converge_services"  # φ8.5: converge

    # ── UPDATE mode phases (φ9-φ13) ──
    SECRETS_UPDATE = "secrets_update"  # φ9: decrypt secrets
    NODE_CONFIG_UPDATE = "node_config_update"  # φ10: read node.yaml, verify core
    REGISTRY_UPDATE = "registry_update"  # φ11: ghcr auth, provision, overlays, llm, healthcheck
    DEPLOY_UPDATE = "deploy_update"  # φ12: deploy modules, ssl, deploy context
    CONVERGE_UPDATE = "converge_update"  # φ13: converge

    # ── Phase sets ──
    INIT_PHASES = frozenset(
        {
            SYSTEM_BOOTSTRAP,
            USER_ACCOUNTS,
            PLATFORM_SETUP,
            SECRETS_PROVISION,
            NODE_CONFIGURATION,
            REGISTRY_AUTH,
            CERTIFICATES,
            DEPLOY_SERVICES,
            CONVERGE_SERVICES,
        }
    )

    UPDATE_PHASES = frozenset(
        {
            SECRETS_UPDATE,
            NODE_CONFIG_UPDATE,
            REGISTRY_UPDATE,
            DEPLOY_UPDATE,
            CONVERGE_UPDATE,
        }
    )

    ALL_PHASES = INIT_PHASES | UPDATE_PHASES

    # ── Ordered phase lists (deterministic execution order) ──
    INIT_PHASE_ORDER: ClassVar[list[str]] = [
        SYSTEM_BOOTSTRAP,
        USER_ACCOUNTS,
        PLATFORM_SETUP,
        SECRETS_PROVISION,
        NODE_CONFIGURATION,
        REGISTRY_AUTH,
        CERTIFICATES,
        DEPLOY_SERVICES,
        CONVERGE_SERVICES,
    ]

    UPDATE_PHASE_ORDER: ClassVar[list[str]] = [
        SECRETS_UPDATE,
        NODE_CONFIG_UPDATE,
        REGISTRY_UPDATE,
        DEPLOY_UPDATE,
        CONVERGE_UPDATE,
    ]

    @classmethod
    def phase_count(cls) -> int:
        """Return total number of phases: 14."""
        return len(cls.ALL_PHASES)

    @classmethod
    def phase_list(cls, mode: str) -> list[str]:
        """Return the ordered phase list for the given mode."""
        if mode == "init":
            return list(cls.INIT_PHASE_ORDER)
        return list(cls.UPDATE_PHASE_ORDER)


# ── Phase dependency graph (DevPlan 087 §2) ──
# Maps each phase to its prerequisite phase(s) that must be done before it can run.
_phase_dependency_graph: dict[str, set[str]] = {
    # INIT mode
    BootstrapPhase.USER_ACCOUNTS: {BootstrapPhase.SYSTEM_BOOTSTRAP},  # φ2 ← φ1
    BootstrapPhase.PLATFORM_SETUP: {BootstrapPhase.USER_ACCOUNTS},  # φ3 ← φ2
    BootstrapPhase.SECRETS_PROVISION: {BootstrapPhase.PLATFORM_SETUP},  # φ4 ← φ3
    BootstrapPhase.NODE_CONFIGURATION: {BootstrapPhase.PLATFORM_SETUP},  # φ5 ← φ3
    BootstrapPhase.REGISTRY_AUTH: {BootstrapPhase.SECRETS_PROVISION},  # φ6 ← φ4
    BootstrapPhase.CERTIFICATES: {BootstrapPhase.NODE_CONFIGURATION},  # φ7 ← φ5
    BootstrapPhase.DEPLOY_SERVICES: {
        BootstrapPhase.SECRETS_PROVISION,
        BootstrapPhase.REGISTRY_AUTH,
        BootstrapPhase.CERTIFICATES,
    },  # φ8 ← φ4, φ6, φ7
    BootstrapPhase.CONVERGE_SERVICES: {BootstrapPhase.DEPLOY_SERVICES},  # φ8.5 ← φ8
    # UPDATE mode
    BootstrapPhase.SECRETS_UPDATE: set(),  # φ9 (no deps — entry point)
    BootstrapPhase.NODE_CONFIG_UPDATE: set(),  # φ10 (no deps)
    BootstrapPhase.REGISTRY_UPDATE: set(),  # φ11 (no deps)
    BootstrapPhase.DEPLOY_UPDATE: {BootstrapPhase.SECRETS_UPDATE, BootstrapPhase.REGISTRY_UPDATE},  # φ12 ← φ9, φ11
    BootstrapPhase.CONVERGE_UPDATE: {BootstrapPhase.DEPLOY_UPDATE},  # φ13 ← φ12
}

# Grouped phases (have sub_steps for granular checkpoint tracking)
# ⚠️ TRAP[DEBT] · 2026-08-01 · MED · execute_grouped_phase() вызывается только из тестов
# · Observed: _run_init_mode()/_run_update_mode() вызывают только execute_phase(); execute_grouped_phase()
#   не имеет ни одного production-caller'а (резюме-разводка удалена в DevPlan 116 B8 U-66, D4).
# · Impact: sub-step SKIP логика (φ1-φ5, φ7, φ12) не используется в реальном pipeline —
#   фазы перевыполняются целиком; D4 оставил функцию + 2 прямых теста.
# · When: during DevPlan 116 B8 — deferred, out of scope
# · Fix-hint: B9 — развести execute_grouped_phase() в run-циклы (_run_init_mode/_run_update_mode)
#   для фаз с sub_steps, либо удалить функцию вместе с консервирующими тестами.


# Import shared modules (DevPlan 081B7 DRIFT elimination)
from core.internal.shared.content_hash import compute_content_hash as _shared_compute_content_hash
from core.internal.shared.docker_auth import ghcr_login as _shared_ghcr_login
from core.internal.shared.telegram_notifier import send_telegram as _shared_send_telegram

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"

# INIT_STEPS / UPDATE_STEPS / *_COUNT removed (DevPlan 091 Wave B, AC8).
# Pre-DevPlan-087 23-step dispatch was consolidated to 14 phases. The numeric-key
# fallback in from_dict() no longer has a caller since __init__ now loads directly
# from BootstrapPhase keys. See comment at L574 for the simplified loading path.


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
    def from_dict(cls, data: dict[str, Any], step_list: list[str] | None = None) -> BootstrapState:
        """Deserialize from dict. Supports backward-compat numeric-key migration.

        When step_list is provided and a dict key is a digit string, it is
        migrated to the corresponding step name: key "13" → index 13 → "ensure_secrets".
        This ensures old state.json files (with numeric keys) are compatible
        with the new name-based key lookup.

        ## @rationale DevPlan 071 Rev 2: Numeric keys caused F1 misalignment
        ##   (shell wrote read-node-yaml at key 13, Python expected ensure_secrets).
        ##   Name-based keys eliminate this. Auto-migration ensures existing state.json
        ##   files from pre-migration boots are readable.
        """
        step_list_local = step_list or []
        steps = {}
        for k, v in data.get("steps", {}).items():
            # Detect old numeric-key format: keys like "1", "2", ...
            if k.isdigit() and step_list_local:
                idx = int(k)
                if 1 <= idx <= len(step_list_local):
                    k = step_list_local[idx - 1]  # Migrate to name-based key
                    logger.info("[IMP:8][StateMachine][from_dict] Migrated numeric key %d → %s", idx, k)
            steps[k] = StepState.from_dict(v)
        return cls(
            mode=data.get("mode", "init"),
            node=data.get("node"),
            current_step=data.get("current_step", 0),
            steps=steps,
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
        )

    # region FUNC_precondition_check
    ## @purpose — Validate intra-phase conditions before execution.
    ##            Each phase has specific preconditions (root access, file existence, etc.).
    ## @io — ⇥ phase_value: str (from BootstrapPhase) → ⎋ None (raises PhasePreconditionError on failure)
    ## @complexity — O(1)
    ## @invariants
    ##   - precondition failures are BLOCKING — phase will not execute
    ##   - Error message is human-readable for operator action
    def precondition_check(self, phase_value: str, core_dir: str | None = None) -> None:
        """Validate preconditions for a given phase value. Raises PhasePreconditionError on failure.

        ## @io — ⇥ phase_value: BootstrapPhase, core_dir: str | None (StateMachine.core_dir
        ##        — единый источник резолюции; fallback: CORE_DIR env → /opt/platform/core)
        ## @invariants
        ##   - core_dir передаётся из StateMachine (self.core_dir, установлен CLI из PLATFORM_ROOT);
        ##     BootstrapState не хранит core_dir — параметр, не атрибут.
        """
        if phase_value == BootstrapPhase.SYSTEM_BOOTSTRAP:
            if os.geteuid() != 0:
                raise PhasePreconditionError(
                    f"Phase {phase_value} (system-bootstrap) requires root access (euid=0), got euid={os.geteuid()}"
                )
            # Verify basic system tools
            for cmd in ("apt-get", "dpkg"):
                if not self._check_command_exists(cmd):
                    raise PhasePreconditionError(f"Phase {phase_value} requires '{cmd}' which is not available")

        elif phase_value == BootstrapPhase.USER_ACCOUNTS:
            # Verify user management tools available
            for cmd in ("useradd", "id", "chown"):
                if not self._check_command_exists(cmd):
                    raise PhasePreconditionError(f"Phase {phase_value} requires '{cmd}' which is not available")

        elif phase_value == BootstrapPhase.SECRETS_PROVISION:
            # Age key must be available for decryption
            age_key = os.environ.get("AGE_SECRET_KEY", "") or os.environ.get("SOPS_AGE_KEY", "")
            if not age_key:
                age_key_file = "/etc/age/key.txt"
                if not os.path.isfile(age_key_file):
                    raise PhasePreconditionError(
                        f"Phase {phase_value} requires AGE_SECRET_KEY env var or "
                        f"{age_key_file} file for secret decryption"
                    )

        elif phase_value == BootstrapPhase.REGISTRY_AUTH:
            # GHCR token is optional but warn if missing
            ghcr_token = os.environ.get("GHCR_PULL_TOKEN", "")
            if not ghcr_token:
                logger.warning(
                    "[IMP:7][precondition] Phase %s: GHCR_PULL_TOKEN not set — "
                    "Docker Hub rate-limit may apply (~100 pulls/6h)",
                    phase_value,
                )

        elif phase_value == BootstrapPhase.NODE_CONFIGURATION:
            node_yaml = os.environ.get("NODE_YAML", "")
            if not node_yaml or not os.path.isfile(node_yaml):
                raise PhasePreconditionError(f"Phase {phase_value} requires valid NODE_YAML path: {node_yaml}")

        elif phase_value == BootstrapPhase.CERTIFICATES:
            # Verify acme.sh or install script available
            # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · precondition искал core по CORE_DIR env (default /opt/platform)
            # · Symptom: φ8 precondition: "deploy-modules.sh at /opt/platform/core/... required" на ноде,
            # ·   где core лежит по mirror-пути PLATFORM_ROOT (см. remote-cmd.sh TRAP[BUG] PLATFORM_ROOT).
            # ·   CORE_DIR env не экспортируется remote-командой — использовался дефолт /opt/platform.
            # · Fix: резолвить core_dir через self.core_dir (уже установлен CLI из PLATFORM_ROOT,
            # ·   строка 1333) — единый источник с _execute_phase (строка 838).
            # · Prevention: не дублировать резолюцию core_dir в прекондишенах — всегда self.core_dir.
            core_dir = core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
            acme_script = os.path.join(core_dir, "internal", "bootstrap", "install-acme.sh")
            if not os.path.isfile(acme_script):
                logger.warning(
                    "[IMP:7][precondition] Phase %s: install-acme.sh not found at %s — acme.sh installation may fail",
                    phase_value,
                    acme_script,
                )

        elif phase_value in (BootstrapPhase.DEPLOY_SERVICES, BootstrapPhase.DEPLOY_UPDATE):
            core_dir = core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
            deploy_script = os.path.join(core_dir, "internal", "bootstrap", "deploy-modules.sh")
            if not os.path.isfile(deploy_script):
                raise PhasePreconditionError(f"Phase {phase_value} requires deploy-modules.sh at {deploy_script}")
            # Docker must be running
            docker_check = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if docker_check.returncode != 0:
                raise PhasePreconditionError(
                    f"Phase {phase_value} requires Docker daemon running: {docker_check.stderr.strip()[:200]}"
                )

        elif phase_value in (BootstrapPhase.CONVERGE_SERVICES, BootstrapPhase.CONVERGE_UPDATE):
            # Converge script must exist
            core_dir = core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
            converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
            if not os.path.isfile(converge_script):
                logger.warning(
                    "[IMP:7][precondition] Phase %s: converge.sh not found at %s — converge will be skipped",
                    phase_value,
                    converge_script,
                )

        # UPDATE mode phases — lighter precondition checks
        elif phase_value == BootstrapPhase.SECRETS_UPDATE:
            # Same as SECRETS_PROVISION but non-blocking (update mode)
            pass

        elif phase_value in (BootstrapPhase.NODE_CONFIG_UPDATE, BootstrapPhase.REGISTRY_UPDATE):
            # Light check — these are optional update steps
            pass

        logger.info(
            "[IMP:8][precondition_check] Phase %s preconditions satisfied",
            phase_value,
        )

    # endregion FUNC_precondition_check

    @staticmethod
    def _check_command_exists(cmd: str) -> bool:
        """Check if a system command is available via `command -v` (bash builtin).

        ▶ ┌cmd┐ → ⚡ bash -c "command -v <cmd>" → ◇ rc!=0? → False | ⎋ True

        ## @purpose — Verify prerequisite command existence for precondition checks.
        ## @io — ⇥ cmd: str → ⎋ bool
        ## @complexity — O(1)
        ## @invariants
        ##   - `command -v` — bash builtin: запускается ЧЕРЕЗ bash -c (не прямым exec)
        ## ⚠️ TRAP[BUG] · 2026-07-31 · P1 · `command -v` через прямой exec НИКОГДА не работал
        ## · Symptom: precondition_check(φ1 system_bootstrap) падал на ЧИСТОЙ ноде:
        ## ·   "Phase system_bootstrap requires 'apt-get' which is not available" —
        ## ·   apt-get существует (which apt-get → /usr/bin/apt-get). E2E DevPlan 095 T6.
        ## · Root: `command` — bash-встроенная (builtin), НЕ исполняемый файл.
        ## ·   subprocess.run(["command", "-v", cmd]) → FileNotFoundError:
        ## ·   "No such file or directory: 'command'" → except → return False.
        ## ·   Функция возвращала False для ЛЮБОЙ команды в ЛЮБОМ окружении —
        ## ·   все command-прекондишены всех фаз были мёртвыми (ложный отказ).
        ## · Fix: /bin/bash -c "command -v <cmd>" — builtin вызывается внутри bash.
        ## ·   (shutil.which отклонён: 3 pre-existing unit-теста мокают subprocess.run —
        ## ·   MagicMock(returncode=0) → контракт моков сохраняется; платформа bash-first.)
        ## · Prevention: не использовать bash builtins через subprocess.run(list) —
        ## ·   builtin обязан вызываться через bash -c.
        ## · Source: обнаружено при верификации DevPlan 095 AC4 (cold-start bootstrap).
        """
        try:
            result = subprocess.run(
                ["/bin/bash", "-c", f"command -v {shlex.quote(cmd)} >/dev/null 2>&1"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False


# endregion DATACLASSES


# region STATEMACHINE


class StateMachine:
    """State machine for bootstrap/update lifecycle steps.

    ## @purpose — Load/create/save state.json, orchestrate step transitions,
    ##            compute content hashes, validate env, dispatch to step implementations.
    ## @scope — Core of W4-E2 decomposition. Manages state transitions only —
    ##          actual step logic lives in phases.py (14 phase implementations).
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
                # DevPlan 091 Wave B (AC8): load directly from BootstrapPhase keys.
                # The old INIT_STEPS/UPDATE_STEPS numeric-key fallback was removed together
                # with the 23-step constants. state.json now contains
                # only phase keys (system_bootstrap, user_accounts, …).
                self.state = BootstrapState.from_dict(data)
                # Phase key migration: copy phase keys from root level into steps dict.
                # This handles migrated state.json files where migrate_state_to_phases()
                # wrote phase keys at root level but from_dict only reads data["steps"].
                for pv in BootstrapPhase.ALL_PHASES:
                    if pv in data and pv not in self.state.steps:
                        self.state.steps[pv] = data[pv]
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
        """Validate preconditions and mark step N as running (name-based key)."""
        step_name = self._step_name(n)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        step = StepState(name=step_name, status="running", started_at=now)
        self.state.steps[step_name] = step
        self.state.current_step = n
        logger.info("[IMP:9][StateMachine][start_step] Step %d (%s) START", n, step_name)
        self.save()

    # endregion FUNC_start_step

    # region FUNC_complete_step
    ## @purpose — Mark a step as done with optional content hash (name-based key).
    ## @io — ⇥ n: step index, hash_val: optional content hash → ⎋ None
    ## @complexity — O(1)
    def complete_step(self, n: int, hash_val: str | None = None) -> None:
        """Mark step N as completed successfully (name-based key)."""
        step_name = self._step_name(n)
        if step_name not in self.state.steps:
            logger.warning("[IMP:7][StateMachine][complete_step] Step %d not started — creating", n)
            self.state.steps[step_name] = StepState(name=step_name)
        self.state.steps[step_name].status = "done"
        if hash_val:
            self.state.steps[step_name].hash = hash_val
        logger.info(
            "[IMP:9][StateMachine][complete_step] Step %d (%s) DONE",
            n,
            self.state.steps[step_name].name,
        )
        self.save()

    # endregion FUNC_complete_step

    # region FUNC_skip_step
    ## @purpose — Mark a step as skipped with reason (TOR_DISABLED, content_unchanged).
    ## @io — ⇥ n: step index, reason: skip reason → ⎋ None
    ## @complexity — O(1)
    def skip_step(self, n: int, reason: str = "") -> None:
        """Mark step N as skipped (not failed, not run) — name-based key."""
        step_name = self._step_name(n)
        if step_name not in self.state.steps:
            self.state.steps[step_name] = StepState(name=step_name)
        self.state.steps[step_name].status = "skipped"
        self.state.steps[step_name].reason = reason or "content_unchanged"
        logger.info(
            "[IMP:9][StateMachine][skip_step] Step %d (%s) SKIPPED: %s",
            n,
            self.state.steps[step_name].name,
            reason,
        )
        self.save()

    # endregion FUNC_skip_step

    # region FUNC_fail_step
    ## @purpose — Mark a step as failed, collect error message.
    ## @io — ⇥ n: step index, error: error description → ⎋ None
    ## @complexity — O(1)
    def fail_step(self, n: int, error: str) -> None:
        """Mark step N as failed (name-based key)."""
        step_name = self._step_name(n)
        if step_name not in self.state.steps:
            self.state.steps[step_name] = StepState(name=step_name)
        self.state.steps[step_name].status = "failed"
        self.state.steps[step_name].error = error
        self.state.errors.append(f"Step {n} ({step_name}): {error}")
        logger.error(
            "[IMP:10][StateMachine][fail_step] Step %d (%s) FAILED: %s",
            n,
            step_name,
            error,
        )
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

        # Find first step that is pending or failed (name-based key lookup)
        for i in range(1, len(step_list) + 1):
            step_name = self._step_name(i)
            step = self.state.steps.get(step_name)
            if step is None:
                return i
            if step.status in ("pending", "failed"):
                return i
            if step.status == "running":
                return i  # re-run hanging steps

        return None

    # endregion FUNC_get_current_step

    # region FUNC__step_hash
    ## @purpose — Compute SHA256 content hash via shared content_hash module.
    ##            Delegates to compute_content_hash() from core.internal.shared.content_hash.
    ## @io — ⇥ step_name: str, extra_paths: list of additional script paths → ⎋ str hexdigest
    ## @complexity — O(S) where S = total file bytes hashed
    def _step_hash(self, step_name: str, *extra_paths: str) -> str:
        """Compute SHA256 content hash via shared content_hash module."""
        paths_to_hash = [os.path.abspath(__file__), *list(extra_paths)]
        digest = _shared_compute_content_hash(paths_to_hash)
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

    # region FUNC_execute_phase
    ## @purpose — Execute a single phase by calling its phase function from phases.py.
    ##            Checks dependency graph first, then precondition, then executes.
    ## @io — ⇥ phase_value: str (from BootstrapPhase) → ⎋ None (raises on failure)
    ## @complexity — O(D + P) where D = dependency check, P = phase execution
    ## @invariants
    ##   - Dependency check before precondition check
    ##   - Phase dependency graph: all prerequisite phases must be done
    ##   - Precondition check: intra-phase conditions verified before execution
    def execute_phase(self, phase_value: str) -> None:
        """Execute a single phase with dependency and precondition checks.

        Steps: dependency graph → precondition check → phase execution → post-condition.
        """
        logger.info("[IMP:9][execute_phase] Starting phase %s", phase_value)

        # Step 1: Check dependency graph
        deps = _phase_dependency_graph.get(phase_value, set())
        missing_deps: list[str] = []
        for dep in deps:
            phase_state = self.state.steps.get(dep, self._state_from_phase_key(dep))
            if isinstance(phase_state, dict):
                phase_done = phase_state.get("done", False)
            else:
                phase_done = getattr(phase_state, "status", "pending") == "done"
            if not phase_done:
                missing_deps.append(dep)

        if missing_deps:
            raise PhaseDependencyError(
                f"Phase '{phase_value}' requires prerequisite phase(s): "
                f"{', '.join(missing_deps)}. "
                f"Execute missing phases first."
            )

        # Step 2: Check preconditions
        self.state.precondition_check(phase_value, core_dir=self.core_dir)

        # Step 3: Dynamic import and execute from phases.py
        try:
            from core.internal.bootstrap.lifecycle.phases import (
                phase_certificates,
                phase_converge_services,
                phase_converge_update,
                phase_deploy_services,
                phase_deploy_update,
                phase_node_config_update,
                phase_node_configuration,
                phase_platform_setup,
                phase_registry_auth,
                phase_registry_update,
                phase_secrets_provision,
                phase_secrets_update,
                phase_system_bootstrap,
                phase_user_accounts,
            )
        except ImportError:
            logger.error("[IMP:10][execute_phase] Cannot import phases module")
            raise

        # Map phase value to function
        phase_func_map = {
            BootstrapPhase.SYSTEM_BOOTSTRAP: phase_system_bootstrap,
            BootstrapPhase.USER_ACCOUNTS: phase_user_accounts,
            BootstrapPhase.PLATFORM_SETUP: phase_platform_setup,
            BootstrapPhase.SECRETS_PROVISION: phase_secrets_provision,
            BootstrapPhase.NODE_CONFIGURATION: phase_node_configuration,
            BootstrapPhase.REGISTRY_AUTH: phase_registry_auth,
            BootstrapPhase.CERTIFICATES: phase_certificates,
            BootstrapPhase.DEPLOY_SERVICES: phase_deploy_services,
            BootstrapPhase.CONVERGE_SERVICES: phase_converge_services,
            BootstrapPhase.SECRETS_UPDATE: phase_secrets_update,
            BootstrapPhase.NODE_CONFIG_UPDATE: phase_node_config_update,
            BootstrapPhase.REGISTRY_UPDATE: phase_registry_update,
            BootstrapPhase.DEPLOY_UPDATE: phase_deploy_update,
            BootstrapPhase.CONVERGE_UPDATE: phase_converge_update,
        }

        phase_func = phase_func_map.get(phase_value)
        if phase_func is None:
            raise PhaseDependencyError(f"Unknown phase: {phase_value}")

        # Execute
        core_dir = self.core_dir or os.environ.get("CORE_DIR", "/opt/platform/core")
        node_name = os.environ.get("NODE_NAME", "")
        node_yaml = os.environ.get("NODE_YAML", "")

        result = phase_func(core_dir, node_name, node_yaml)
        logger.info(
            "[IMP:9][execute_phase] Phase %s completed: %s",
            phase_value,
            "success" if result else "with warnings",
        )

    # endregion FUNC_execute_phase

    # region FUNC_execute_grouped_phase
    ## @purpose — Execute a grouped phase with sub-checkpoint support.
    ##            Checks each sub_step individually; skips unchanged+done sub_steps.
    ## @io — ⇥ phase_value: str, sub_steps: dict[str, dict] → ⎋ bool (True = all done)
    ## @complexity — O(S * H) where S = sub_steps, H = hash computation
    ## @invariants
    ##   - Sub-steps with done=true + unchanged hash → SKIP (not executed)
    ##   - Sub-steps with done=false or changed hash → EXECUTE
    ##   - Phase is done=true only when ALL sub_steps are done
    def execute_grouped_phase(self, phase_value: str, sub_steps: dict[str, dict] | None = None) -> bool:
        """Execute a grouped phase, checking each sub-step individually for skip/execute.

        Returns True if phase is now fully done, False if partial failure.
        """
        logger.info("[IMP:9][execute_grouped_phase] Starting grouped phase %s", phase_value)

        # Check dependencies first
        deps = _phase_dependency_graph.get(phase_value, set())
        for dep in deps:
            phase_state = self.state.steps.get(dep, {})
            if isinstance(phase_state, dict):
                phase_done = phase_state.get("done", False)
            else:
                phase_done = getattr(phase_state, "status", "pending") == "done"
            if not phase_done:
                raise PhaseDependencyError(f"Grouped phase '{phase_value}' requires prerequisite '{dep}'")

        # Check preconditions
        self.state.precondition_check(phase_value, core_dir=self.core_dir)

        if sub_steps is None:
            logger.info("[IMP:7][execute_grouped_phase] No sub_steps for %s — running as simple phase", phase_value)
            self.execute_phase(phase_value)
            return True

        all_done = True
        for sub_name, sub_state in sub_steps.items():
            sub_done = sub_state.get("done", False)
            sub_hash = sub_state.get("hash", "")

            # Compute current hash for this sub-step
            current_hash = self._step_hash(f"sub_{phase_value}_{sub_name}")

            if sub_done and sub_hash and sub_hash == current_hash:
                logger.info(
                    "[IMP:8][execute_grouped_phase][%s] SKIP sub_step '%s' (unchanged hash=%s)",
                    phase_value,
                    sub_name,
                    sub_hash[:12],
                )
                continue

            # Execute the sub-step
            logger.info(
                "[IMP:9][execute_grouped_phase][%s] EXECUTE sub_step '%s' (done=%s, hash_changed=%s)",
                phase_value,
                sub_name,
                sub_done,
                sub_hash and sub_hash != current_hash,
            )

            try:
                # Run the full phase for this sub-step (each phase function handles all its sub-steps)
                self.execute_phase(phase_value)
                logger.info(
                    "[IMP:9][execute_grouped_phase][%s] Sub_step '%s' completed",
                    phase_value,
                    sub_name,
                )
            except (PhaseDependencyError, PhasePreconditionError, PlatformError) as e:
                logger.error(
                    "[IMP:10][execute_grouped_phase][%s] Sub_step '%s' FAILED: %s",
                    phase_value,
                    sub_name,
                    e,
                )
                all_done = False

        return all_done

    # endregion FUNC_execute_grouped_phase

    def _state_from_phase_key(self, phase_key: str) -> dict:
        """Get phase state from state dict using the phase key directly.

        ## @purpose — Helper to look up phase state in both old (steps dict) and new format.
        ## @io — ⇥ phase_key: str → ⎋ dict (state entry or empty)
        ## @complexity — O(1)
        """
        step_entry = self.state.steps.get(phase_key)
        if step_entry is not None:
            if hasattr(step_entry, "to_dict"):
                return step_entry.to_dict()
            if isinstance(step_entry, dict):
                return step_entry
            return {"status": step_entry.status if hasattr(step_entry, "status") else "pending"}

        # Also check top-level state for phase keys
        state_dict = getattr(self.state, "state_dict", None)
        if state_dict is None:
            try:
                state_dict = self.state.to_dict()
            except Exception:  # noqa: EXC — best-effort
                return {}
        return state_dict.get("steps", {}).get(phase_key, {})

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
        """Return the step/phase list for the current mode.

        Now returns phase-based list (BootstrapPhase.INIT_PHASE_ORDER
        or UPDATE_PHASE_ORDER) instead of the old INIT_STEPS/UPDATE_STEPS.
        Old step constants are preserved as deprecated.
        """
        return BootstrapPhase.phase_list(self.state.mode)

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
        """Check if step N is already completed (name-based key lookup)."""
        step_name = self._step_name(n)
        step = self.state.steps.get(step_name)
        return step is not None and step.status == "done"

    # endregion FUNC__is_step_done

    # region FUNC__is_step_skipped
    def _is_step_skipped(self, n: int) -> bool:
        """Check if step N is skipped (name-based key lookup)."""
        step_name = self._step_name(n)
        step = self.state.steps.get(step_name)
        return step is not None and step.status == "skipped"

    # endregion FUNC__is_step_skipped

    # region FUNC__hash_changed
    def _hash_changed(self, n: int, new_hash: str) -> bool:
        """Check if step hash changed since last run (name-based key lookup)."""
        step_name = self._step_name(n)
        step = self.state.steps.get(step_name)
        if step is None:
            return True
        return step.hash != new_hash

    # endregion FUNC__hash_changed

    # region FUNC__check_precondition
    ## @purpose — Validate pre-condition before executing a step (W5-E6 C3).
    ##            Asserts previous step (n-1) is in {done, skipped} or n == 1.
    ## @io — ⇥ state: BootstrapState, step_index: int, step_name: str
    ##       ⎋ None (raises StateTransitionError on violation)
    ## @complexity — O(1)
    def _check_precondition(self, state: BootstrapState, step_index: int, step_name: str) -> None:
        """Assert previous step is done/skipped (or step_index == 1 for first step).
        Uses name-based key lookup for step state.
        """
        if step_index == 1:
            # First step — no previous to check
            logger.debug(
                "[IMP:6][StateMachine][_check_precondition] Step %d (%s): first step — pre-condition OK",
                step_index,
                step_name,
            )
            return
        prev_name = self._step_name(step_index - 1)
        prev_step = state.steps.get(prev_name)
        if prev_step is None:
            raise StateTransitionError(
                f"Pre-condition violation: step {step_index - 1} ({prev_name}) has no state (never started). "
                f"Cannot execute step {step_index} ({step_name})."
            )
        if prev_step.status not in ("done", "skipped"):
            raise StateTransitionError(
                f"Pre-condition violation: step {step_index - 1} status is '{prev_step.status}', "
                f"expected 'done' or 'skipped'. Cannot execute step {step_index} ({step_name})."
            )
        logger.debug(
            "[IMP:6][StateMachine][_check_precondition] Step %d (%s): pre-condition OK (prev=%s)",
            step_index,
            step_name,
            prev_step.status,
        )

    # endregion FUNC__check_precondition

    # region FUNC__check_postcondition
    ## @purpose — Validate post-condition after completing a step (W5-E6 C3).
    ##            Asserts current step status == done, state.current_step == step_index.
    ## @io — ⇥ state: BootstrapState, step_index: int, step_name: str
    ##       ⎋ None (raises StateTransitionError on violation)
    ## @complexity — O(1)
    def _check_postcondition(self, state: BootstrapState, step_index: int, step_name: str) -> None:
        """Assert current step is done and state.current_step matches step_index.
        Uses name-based key lookup for step state.
        """
        step = state.steps.get(step_name)
        if step is None:
            raise StateTransitionError(f"Post-condition violation: step {step_index} ({step_name}) has no state entry.")
        if step.status != "done":
            raise StateTransitionError(
                f"Post-condition violation: step {step_index} ({step_name}) status is '{step.status}', expected 'done'."
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
    ## @purpose — Initialize state for a run: set mode, node, create phase entries.
    ## @io — ⇥ mode: str, node: Optional[str] → ⎋ None
    ## @complexity — O(N) where N = number of phases
    def setup_state(self, mode: str, node: str | None = None) -> None:
        """Initialize state for a new run with phase-based keys.

        Sets mode, node, creates MISSING phase entries as pending (existing preserved).
        """
        # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · setup_state сбрасывал ВСЕ фазы в pending — идемпотентность мёртва
        # · Symptom: повторный `make bootstrap-node` ПЕРЕВЫПОЛНЯЛ все 9 INIT фаз (~10 мин) без
        # ·   SKIP-логов; E2E DevPlan 095 T13 (idempotent rebootstrap): "skip markers found=0" при exit 0.
        # ·   Триггер: CLI (строка ~1397) вызывал setup_state при current_step==0 — а phase-based
        # ·   машина НЕ инкрементит current_step (всегда 0) → setup_state на КАЖДОМ запуске.
        # · Root: docstring "Always reset all phase entries to pending" — наследие step-based машины;
        # ·   run_init/run_update already-done-проверки никогда не могли сработать (фазы всегда pending).
        # · Fix: setdefault-семантика — существующие записи фаз сохраняются (done остаётся done),
        # ·   недостающие (другой mode) создаются pending. Полный сброс = --force (reset()).
        # · Prevention: setup_state никогда не должен стирать done-состояния без явного --force;
        # ·   unit-контракт: повторный setup_state(mode) не меняет существующие статусы.
        self.state.mode = mode
        self.state.node = node
        self.state.current_step = 0
        phase_list = self._step_list()  # now returns BootstrapPhase phase list
        # Create missing phase entries as pending; preserve existing (idempotency)
        for phase_val in phase_list:
            existing = self.state.steps.get(phase_val)
            if existing is None:
                self.state.steps[phase_val] = StepState(name=phase_val, status="pending")
        logger.info(
            "[IMP:8][StateMachine][setup_state] State initialized: mode=%s node=%s phases=%d (existing preserved)",
            mode,
            node,
            len(phase_list),
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
        """Generate a phase-based dry-run execution plan (no mutations)."""
        phase_list = self._step_list()  # returns BootstrapPhase phase list
        total = len(phase_list)
        lines: list[str] = []
        lines.append(f"===== DRY RUN: {self.state.mode} mode ({total}-phase) =====")
        lines.append(f"NODE: {self.state.node or '<unset>'}")
        lines.append("Phases:")
        for i, phase_val in enumerate(phase_list, 1):
            phase_state = self.state.steps.get(phase_val)
            if phase_state is not None:
                if isinstance(phase_state, dict):
                    status = "done" if phase_state.get("done") else "pending"
                else:
                    status = getattr(phase_state, "status", "pending")
                lines.append(f"  {i}. {phase_val} [{status}]")
            else:
                lines.append(f"  {i}. {phase_val} [pending]")
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

    ## @purpose — CLI entry point for state_machine.py. Supports --mode, --run-phase,
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
    parser.add_argument(
        "--run-phase",
        type=str,
        help="Run a specific phase by name (e.g., system_bootstrap, secrets_provision, deploy_services)",
    )
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
    ##            Handles: --dry-run, --force, --resume, --run-phase, mode dispatch.
    ## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
    ## @complexity — O(N * M) where N = phases, M = per-phase operations
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

    # ── REMOVED (DevPlan 091 Wave B, User Constraint): state migration block ──
    # The one-shot `migrate_state_to_phases()` call (old 23-step keys → 14-phase keys)
    # was removed in the same wave. User Constraint: тестовая фаза,
    # можно ронять. state.json создаётся с нуля при cold start bootstrap; старые файлы
    # с numeric keys больше не поддерживаются. Если на production-ноде ещё остался старый
    # state.json с 23 keys — оператор должен вручную удалить его перед обновлением.
    # ⚠️ TRAP[DECISION] · 2026-07-30 · HI · Removed state migration — cold start only
    # · Rejected: keep migration for backward-compat (risk: dead code rotting, 198 LOC untested)
    # · Reason: тестовая фаза (AGENTS.md инвариант 9). Cold start = 9 INIT фаз создают новый
    #   state.json. Production rollout — перед обновлением: rm /var/lib/platform/.bootstrap/state.json
    # · Rev: если production-нода не может быть пересоздана → восстановить миграцию как отдельный скрипт,
    #   не в hot path bootstrap.

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

    # ── --run-phase: execute single phase ──
    if args.run_phase:
        if args.run_phase not in BootstrapPhase.ALL_PHASES:
            logger.error(
                "[IMP:10][main] Unknown phase '%s'. Valid phases: %s",
                args.run_phase,
                ", ".join(sorted(BootstrapPhase.ALL_PHASES)),
            )
            return 1
        logger.info("[IMP:9][main] Running single phase: %s", args.run_phase)
        try:
            sm.execute_phase(args.run_phase)
            logger.info("[IMP:9][main] Phase '%s' completed successfully", args.run_phase)
            return 0
        except (PhaseDependencyError, PhasePreconditionError, PlatformFatalError) as e:
            logger.error("[IMP:10][main] Phase '%s' FAILED: %s", args.run_phase, e)
            return 1

    # ── Dispatch full mode run ──
    try:
        if args.mode == "init":
            return _run_init_mode(sm)
        return _run_update_mode(sm)
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code


# endregion MAIN


# region RUN_INIT_MODE
def _run_init_mode(sm: StateMachine) -> int:
    """Execute all init mode phases (9 phases from BootstrapPhase enum).

    ## @purpose — Run 9 init phases sequentially with dependency checking.
    ## @io — ⇥ sm: StateMachine → ⎋ int exit code
    ## @complexity — O(N * M) where N = 9 phases, M = per-phase operations
    """
    init_phases = BootstrapPhase.INIT_PHASE_ORDER
    total = len(init_phases)
    for i, phase in enumerate(init_phases, 1):
        logger.info("[IMP:9][run_init] Phase %d/%d: %s", i, total, phase)

        # ── Check if already done ──
        phase_state = sm.state.steps.get(phase)
        if phase_state is not None:
            if isinstance(phase_state, dict):
                # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · loaded state.json (StepState dict: {name,status,hash})
                # · не содержит ключа "done" → проверка phase_state.get("done") всегда False →
                # · повторный bootstrap ПЕРЕВЫПОЛНЯЛ все 9 фаз (без SKIP-логов, ~10 мин лишних).
                # · E2E DevPlan 095 T13 (idempotent rebootstrap): skip markers found=0 при exit 0.
                # · Fix: status=="done" учитывается и для dict-представления (StepState.to_dict).
                if phase_state.get("done", False) or phase_state.get("status") == "done":
                    logger.info("[IMP:7][run_init] Phase %s already done — skipping", phase)
                    continue
            elif phase_state.status == "done":
                logger.info("[IMP:7][run_init] Phase %s already done — skipping", phase)
                continue

        try:
            sm.execute_phase(phase)
            # Mark phase as done in state
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["done"] = True
                entry["status"] = "done"
            else:
                sm.state.steps[phase] = StepState(name=phase, status="done")
            sm.save()
            logger.info("[IMP:9][run_init] Phase %s completed successfully", phase)
        except PhaseDependencyError as e:
            logger.error("[IMP:10][run_init] Dependency error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code if hasattr(e, "exit_code") else 1
        except PhasePreconditionError as e:
            logger.error("[IMP:10][run_init] Precondition failed for phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return 1
        except PlatformFatalError as e:
            logger.critical("[IMP:10][run_init] Fatal error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code

    logger.info("[IMP:10][run_init] All %d init phases completed successfully", total)
    return 0


# endregion RUN_INIT_MODE


# region RUN_UPDATE_MODE
def _run_update_mode(sm: StateMachine) -> int:
    """Execute all update mode phases (5 phases from BootstrapPhase enum).

    ## @purpose — Run 5 update phases sequentially with dependency checking.
    ## @io — ⇥ sm: StateMachine → ⎋ int exit code
    ## @complexity — O(N * M) where N = 5 phases, M = per-phase operations
    """
    update_phases = BootstrapPhase.UPDATE_PHASE_ORDER
    total = len(update_phases)
    for i, phase in enumerate(update_phases, 1):
        logger.info("[IMP:9][run_update] Phase %d/%d: %s", i, total, phase)

        # ── Check if already done ──
        phase_state = sm.state.steps.get(phase)
        if phase_state is not None:
            if isinstance(phase_state, dict):
                if phase_state.get("done", False):
                    logger.info("[IMP:7][run_update] Phase %s already done — skipping", phase)
                    continue
            elif phase_state.status == "done":
                logger.info("[IMP:7][run_update] Phase %s already done — skipping", phase)
                continue

        try:
            sm.execute_phase(phase)
            # Mark phase as done in state
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["done"] = True
                entry["status"] = "done"
            else:
                sm.state.steps[phase] = StepState(name=phase, status="done")
            sm.save()
            logger.info("[IMP:9][run_update] Phase %s completed successfully", phase)
        except PhaseDependencyError as e:
            logger.error("[IMP:10][run_update] Dependency error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code if hasattr(e, "exit_code") else 1
        except PhasePreconditionError as e:
            logger.error("[IMP:10][run_update] Precondition failed for phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return 1
        except PlatformFatalError as e:
            logger.critical("[IMP:10][run_update] Fatal error in phase %s: %s", phase, e)
            entry = sm.state.steps.get(phase)
            if isinstance(entry, dict):
                entry["status"] = "failed"
            elif entry is not None:
                entry.status = "failed"
            sm.save()
            return e.exit_code

    logger.info("[IMP:10][run_update] All %d update phases completed successfully", total)
    return 0


# endregion RUN_UPDATE_MODE


# region RUN_STEPS (DEPRECATED)
## @deprecated — Replaced by phase-based _run_init_mode()/_run_update_mode()
##               which call execute_phase() directly. Old step-based dispatch
##               (30 elif branches) is removed. All execution goes through
##               phase functions in phases.py.
# endregion RUN_STEPS


# region EXECUTE_UPDATE_STEP (DEPRECATED — removed, now in phases.py)
## @deprecated — Old 9-step update dispatch removed (DevPlan 087).
##               All update execution goes through phase functions in phases.py.
# endregion EXECUTE_UPDATE_STEP


# region HELPER_FUNCTIONS


# region FUNC__import_deploy_context
## @purpose — Import and run context_deployer.deploy_context() via importlib.
##            DevPlan 079: replaces _steps._step_deploy_context().
## @io — ⇥ core_dir: str, node_name: str, node_yaml: str → ⎋ None (non-fatal)
## @complexity — O(D * P) where D = domains, P = projects
def _import_deploy_context(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Import context_deployer.deploy_context() via importlib and execute."""
    try:
        import importlib.util

        deployer_path = os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py")
        spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)
        if spec and spec.loader:
            deployer_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(deployer_mod)
            result = deployer_mod.deploy_context(core_dir, node_name, node_yaml)
            logger.info(
                "[IMP:9][deploy_context] Complete: deployed=%d skipped=%d failed=%d",
                result.deployed if result else 0,
                result.skipped if result else 0,
                result.failed if result else 0,
            )
        else:
            logger.warning("[IMP:7][deploy_context] Cannot load context_deployer.py")
    except Exception as e:  # noqa: EXC — non-fatal: deploy_context is best-effort
        logger.warning("[IMP:7][deploy_context] deploy_context failed (non-fatal): %s", e)


# endregion FUNC__import_deploy_context


# region FUNC__import_extract_domains
## @purpose — Extract domains via context_deployer._extract_domains_for_context.
##            DevPlan 079: replaces _extract_domains() in state_machine.
## @io — ⇥ core_dir: str, node_yaml: str, context: str → ⎋ list[str]
## @complexity — O(N) YAML parse
def _import_extract_domains(core_dir: str, node_yaml: str, context: str) -> list[str]:
    """Extract domains via context_deployer._extract_domains_for_context."""
    try:
        import importlib.util

        deployer_path = os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py")
        spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)
        if spec and spec.loader:
            deployer_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(deployer_mod)
            return deployer_mod._extract_domains_for_context(node_yaml, context)
    except Exception as e:  # noqa: EXC — catch-all for importlib-based calls (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ssl_provision] Failed to extract domains: %s", e)
    return []


# endregion FUNC__import_extract_domains


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
                raise PlatformFatalError(f"Command not found (exit=127): {err_msg}")
            if non_fatal:
                logger.warning("[IMP:7][subprocess][%s] %s", step_name, err_msg)
            elif check_required:
                raise PlatformFatalError(err_msg)
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
        raise PlatformFatalError(msg) from None
    except FileNotFoundError:
        msg = f"Command not found: {cmd[0]}"
        if non_fatal:
            logger.warning("[IMP:7][subprocess][%s] %s", step_name, msg)
            return subprocess.CompletedProcess(cmd, -1, "", msg)
        raise PlatformFatalError(msg) from None


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
    except (PlatformError, subprocess.TimeoutExpired) as e:
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
        raise ConfigNotFoundError(
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
        # lib/secrets.sh requires CORE_DIR, logging.sh (log_step). step_start/done/skip are
        # self-contained via declare -f stub-guard (secrets.sh L117-121) — checkpoint.sh
        # removed in DevPlan 091, no longer sourced (DevPlan 093 W2-T2/W2-T3 preverified).
        # ⚠️ TRAP[BUG] · 2026-07-23 · P0 · source secrets.sh без зависимостей
        # · Symptom: step_start/log_step: command not found, CORE_DIR/internal/... : No such file
        # · Root: bash -c "source secrets.sh" не имел CORE_DIR и не подгружал checkpoint/logging libs
        # · Fix: export CORE_DIR, source logging.sh перед secrets.sh (checkpoint.sh удалён в 091)
        logging_lib = os.path.join(core_dir, "lib", "logging.sh")
        _subprocess_run(
            [
                "bash",
                "-c",
                f"export CORE_DIR={shlex.quote(core_dir)}"
                f" && source {shlex.quote(logging_lib)}"
                f" && source {shlex.quote(secrets_lib)}"
                f" && step_10_decrypt_secrets",
            ],
            "decrypt_secrets",
        )


def _ensure_secrets_exist(core_dir: str) -> None:
    """Ensure secrets.env exists AND all autogen secrets are generated.

    ## @purpose — F2 fix: Read secrets.env, source into os.environ,
    ##            then generate missing autogen secrets via secrets_manager.
    ## @io — ⇥ core_dir: str → ⎋ None (raises RuntimeError if secrets.env missing)
    ## @complexity — O(N) where N = secrets in manifest
    """
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # Step 1: Check file exists (after decrypt)
    if not os.path.isfile(secrets_env):
        # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · Чистая нода без secrets не могла забутстрапиться
        # · Symptom: φ4 secrets_provision FATAL на ноде без AGE-секретов: "secrets.env not found:
        # ·   /run/platform/secrets.env" — decrypt SKIP (нет enc-файла) → env не создан →
        # ·   ensure падал ConfigNotFoundError. E2E DevPlan 095 T6 (node-configs/test-e2e без secrets/).
        # · Root: step_10_decrypt_secrets SKIP'ается при отсутствии <node>.enc.yaml (lib/secrets.sh),
        # ·   но _ensure_secrets_exist требовал secrets.env безусловно. Autogen-механизм
        # ·   (secrets_manager.ensure_secrets → Step 3.5) сам создаёт secrets.env из
        # ·   secrets-manifest.yaml — блокировал только этот ранний raise.
        # · Fix: env отсутствует + НЕТ enc-файла → нода без операторских секретов → SKIP до autogen
        # ·   (ensure создаст файл из манифеста). env отсутствует + enc ЕСТЬ → decrypt FAILED → FATAL.
        # · Prevention: no-secrets нода (modules=[], без secrets/) — валидное состояние; FATAL только
        # ·   при реальном сбое расшифровки.
        node_name = os.environ.get("NODE_NAME", "")
        configs_dir = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
        enc_file = os.path.join(configs_dir, "secrets", f"{node_name}.enc.yaml")
        if os.path.isfile(enc_file):
            logger.error("[IMP:9][ensure_secrets] %s not found after decrypt — cannot generate secrets", secrets_env)
            raise ConfigNotFoundError(f"secrets.env not found: {secrets_env}")
        logger.info("[IMP:8][ensure_secrets] No encrypted secrets for node='%s' — autogen-only secrets.env", node_name)

    # Step 2: Source secrets.env into os.environ
    try:
        from .secrets_manager import source_secrets_env  # type: ignore[import]

        env_vars = source_secrets_env(secrets_env)
        for k, v in env_vars.items():
            if k not in os.environ:
                os.environ[k] = v
        logger.info("[IMP:9][ensure_secrets] Sourced %d vars from %s", len(env_vars), secrets_env)
    except Exception as e:  # noqa: EXC — non-fatal: secrets source failure is recoverable (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ensure_secrets] Failed to source secrets.env: %s", e)

    # Step 3: Generate missing autogen secrets
    manifest_path = os.path.join(core_dir, "secrets-manifest.yaml")
    try:
        from .secrets_manager import ensure_secrets as do_ensure  # type: ignore[import]

        generated = do_ensure(manifest_path, secrets_env)
        if generated:
            logger.info("[IMP:9][ensure_secrets] Generated %d secrets: %s", len(generated), generated)
    except Exception as e:  # noqa: EXC — non-fatal: autogen failure is recoverable (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ensure_secrets] Autogen failed: %s", e)


def _validate_node_yaml(node_yaml: str, core_dir: str) -> None:
    """Validate node.yaml against node.schema.json.

    ## @purpose — Schema validation of node.yaml using jsonschema library (inline python3).
    ## @io — ⇥ node_yaml, core_dir → ⎋ None (raises RuntimeError on invalid)
    ## @complexity — O(1) for schema load + validation
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml}")

    schema_file = os.path.join(core_dir, "schemas", "node.schema.json")
    if not os.path.isfile(schema_file):
        logger.warning("[IMP:7][validate_node_yaml] Schema file not found at %s — skipping validation", schema_file)
        return

    try:
        import jsonschema

        from core.internal.shared.node_yaml import NodeYaml

        with open(schema_file) as f:
            schema = json.load(f)
        instance = NodeYaml(node_yaml).raw()
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
import json, sys
from core.internal.shared.node_yaml import NodeYaml
import jsonschema
instance = NodeYaml('{node_yaml}').raw()
with open('{schema_file}') as f:
    schema = json.load(f)
jsonschema.validate(instance, schema)
""",
            ],
            "validate_node_yaml",
            non_fatal=True,
        )
    except (json.JSONDecodeError, jsonschema.ValidationError) as e:
        logger.warning("[IMP:7][validate_node_yaml] node.yaml validation failed: %s", e)


def _ghcr_auth() -> None:
    """Configure GHCR docker login for ci-deploy user.

    ## @purpose — Docker login to ghcr.io using GHCR_PULL_TOKEN for ci-deploy user.
    ## @io — ⇥ None → ⎋ None (non-fatal if token not set)
    ## @complexity — O(1)
    ## @changes 2026-07-30 | T20a — Replaced inline subprocess with shared docker_auth.ghcr_login()
    ##           (runs as root directly, no sudo needed in bootstrap context)
    """
    token = os.environ.get("GHCR_PULL_TOKEN", "")
    if not token:
        logger.info("[IMP:7][ghcr_auth] GHCR_PULL_TOKEN not set — skipping ghcr auth")
        return
    success = _shared_ghcr_login(token, user="ci-deploy")
    if success:
        logger.info("[IMP:9][ghcr_auth] GHCR auth successful")


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
        raise PlatformFatalError(
            f"{errors} sudoers file(s) with wrong owner/permissions. Fix:\n"
            f"  chown root:root {sudoers_d}/*\n"
            f"  chmod 0440 {sudoers_d}/*"
        )
    logger.info("[IMP:9][sudoers] All sudoers files validated: owner=root:root, mode≤0440")


def _ssl_provision_via_orchestrator(core_dir: str, node_yaml: str) -> None:
    """Provision SSL certs via cert_orchestrator (unified entrypoint).

    Replaces the old _ssl_provision() which had broken S3 credential propagation
    and only handled platform domain. Now delegates to cert_orchestrator for
    ALL domains (platform + projects). Uses direct s3_ssl_cache import (no subshell).

    ## @purpose — Unified cert orchestration via cert_orchestrator.orchestrate_certs().
    ##            Extracts ALL domains from node.yaml (platform + all projects),
    ##            calls cert_orchestrator with direct s3_ssl_cache import.
    ## @io — ⇥ core_dir: str, node_yaml: str → ⎋ None
    ## @complexity — O(D * T) where D = domains, T = timeout per operation
    ## @rationale DevPlan 052 §4.1: Replace _ssl_provision() with cert_orchestrator
    ##           to fix subshell credential loss and handle all domains (not just platform).
    ## @invariants
    ##   - _source_secrets_env() is called inside cert_orchestrator for WEBNAMES_API_KEY
    ##   - S3 credentials are read directly by s3_ssl_cache from os.environ — no subshell
    ##   - context="" means ALL domains (no filtering)
    ##   - Dynamic import allows cert_orchestrator to be updated independently
    """
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # Extract ALL domains (platform + all projects, no context filter) via context_deployer
    context = ""  # empty = no filtering, all domains
    domains = _import_extract_domains(core_dir, node_yaml, context)

    if not domains:
        logger.warning("[IMP:7][ssl_provision] No domains found in node.yaml — skipping")
        return

    issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # Dynamic import of cert_orchestrator
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "cert_orchestrator",
        os.path.join(bootstrap_dir, "cert_orchestrator.py"),
    )
    if spec and spec.loader:
        cert_mod = importlib.util.module_from_spec(spec)
        sys.modules["cert_orchestrator"] = cert_mod
        spec.loader.exec_module(cert_mod)
        cert_result = cert_mod.orchestrate_certs(domains, issue_cert_script, secrets_env, migrate_cron=True)
        logger.info("[IMP:9][ssl_provision] Cert orchestration complete: %s", cert_result.to_dict())
    else:
        logger.warning("[IMP:7][ssl_provision] Cannot load cert_orchestrator.py")


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
        from core.internal.shared.node_yaml import NodeYaml

        node = NodeYaml(node_yaml)
        modules = node.get("modules", default={})
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
        logger.warning("[IMP:7][healthcheck] NodeYaml library not available — skipping inline healthchecks")
    except (ConfigNotFoundError, ConfigParseError, ConfigValidationError) as e:
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
    ## @changes 2026-07-30 | T19 — Replaced inline urllib with shared telegram_notifier.send_telegram()
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
    success = _shared_send_telegram(msg, bot_token, chat_id, proxy_url)
    if success:
        logger.info("[IMP:9][telegram] Notification sent to chat %s", chat_id)


# endregion HELPER_FUNCTIONS


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PlatformError as e:
        logger.critical("[IMP:10][__main__] Platform error (exit=%d): %s", e.exit_code, e)
        print(f"{e}", file=sys.stderr)
        sys.exit(e.exit_code)
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        logger.critical("[IMP:10][__main__] Unexpected error: %s", e)
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)
