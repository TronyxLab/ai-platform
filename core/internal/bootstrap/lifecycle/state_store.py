#!/usr/bin/env python3
# GREP_SUMMARY: state-store, step-state, bootstrap-state, state-json, load-state, save-state, persistence, checkpoint
# STRUCTURE: ▶ StepState (name/status/hash/timing/warnings) → BootstrapState (mode/node/steps/errors/warnings + precondition_check) → ⚡ load_state(path) ┌json.load + phase-key migration┐ → ⚡ save_state(state, path) ┌atomic tmp+replace┐ → ⎋
# region MODULE_CONTRACT
## @purpose  State persistence для bootstrap lifecycle — StepState/BootstrapState dataclasses +
##           state.json I/O (load_state/save_state), извлечено из state_machine (B9 T2, U-08).
## @scope    state_store.py: StepState, BootstrapState (включая precondition_check и
##           _check_command_exists), load_state(path), save_state(state, path).
##           state_machine.py импортирует и re-экспортирует BootstrapState/StepState —
##           публичный контракт пакета (тесты и cli.py не меняют импорты).
## @invariants
##   - BootstrapState.from_dict поддерживает backward-compat numeric-key миграцию (DevPlan 071 Rev 2)
##   - precondition_check — BLOCKING intra-phase валидация; ошибки человекочитаемы
##   - save_state — атомарная запись (tmp + replace)
##   - НЕТ статического импорта state_machine (направление зависимостей: state_machine → state_store);
##     PhasePreconditionError импортируется лениво внутри precondition_check (единственная точка raise)
## @rationale DevPlan 116 B9 D2: persistence (~270 LOC) вынесена в state_store.py —
##            state_machine.py остаётся чистой оркестрацией (~950 LOC, запас под гейт ≤1200).
## @changes  2026-08-01 · Extracted from state_machine (B9 T2)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# region FUNC_StepState
## @purpose  State of a single bootstrap step — status/hash/timing/error/warnings.
## @io       ⇥ constructor params → ⎋ StepState instance with serializable fields
## @complexity O(1)
@dataclass
class StepState:
    """State of a single bootstrap step.

    ## @invariants
    ##   - status ∈ {pending, running, done, skipped, failed, done_with_warnings}
    ##   - done_with_warnings (волна 117 D5): фаза завершилась с non-fatal issues —
    ##     НЕ считается done, перевыполняется при следующем init
    ##   - warnings: per-phase non-fatal issue messages (сохраняются в state.json)
    """

    name: str
    status: str = "pending"  # pending | running | done | skipped | failed | done_with_warnings
    hash: str | None = None  # content hash for idempotency check
    started_at: str | None = None  # ISO timestamp
    error: str | None = None  # error message if failed
    reason: str | None = None  # skip reason (TOR_DISABLED, content_unchanged)
    warnings: list[str] = field(default_factory=list)  # non-fatal issues (волна 117 D5)

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
        if self.warnings:
            d["warnings"] = self.warnings
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
            warnings=data.get("warnings", []),
        )


# endregion FUNC_StepState


# region FUNC_BootstrapState
## @purpose  Serializable root state object for the entire lifecycle run.
## @io       ⇥ constructor params → ⎋ BootstrapState with steps dict
## @complexity O(N) where N = number of steps
@dataclass
class BootstrapState:
    """Complete bootstrap/update state."""

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
        ##   - PhasePreconditionError импортируется лениво (state_store НЕ импортирует state_machine
        ##     статически — направление зависимостей state_machine → state_store; единственная
        ##     точка raise этой ошибки — внутри данного метода, ленивый импорт безопасен).
        """
        from core.internal.bootstrap.lifecycle.state_machine import PhasePreconditionError

        if phase_value == "system_bootstrap":
            if os.geteuid() != 0:
                raise PhasePreconditionError(
                    f"Phase {phase_value} (system-bootstrap) requires root access (euid=0), got euid={os.geteuid()}"
                )
            # Verify basic system tools
            for cmd in ("apt-get", "dpkg"):
                if not self._check_command_exists(cmd):
                    raise PhasePreconditionError(f"Phase {phase_value} requires '{cmd}' which is not available")

        elif phase_value == "user_accounts":
            # Verify user management tools available
            for cmd in ("useradd", "id", "chown"):
                if not self._check_command_exists(cmd):
                    raise PhasePreconditionError(f"Phase {phase_value} requires '{cmd}' which is not available")

        elif phase_value == "secrets_provision":
            # Age key must be available for decryption
            age_key = os.environ.get("AGE_SECRET_KEY", "") or os.environ.get("SOPS_AGE_KEY", "")
            if not age_key:
                age_key_file = "/etc/age/key.txt"
                if not os.path.isfile(age_key_file):
                    raise PhasePreconditionError(
                        f"Phase {phase_value} requires AGE_SECRET_KEY env var or "
                        f"{age_key_file} file for secret decryption"
                    )

        elif phase_value == "registry_auth":
            # GHCR token is optional but warn if missing
            ghcr_token = os.environ.get("GHCR_PULL_TOKEN", "")
            if not ghcr_token:
                logger.warning(
                    "[IMP:7][precondition] Phase %s: GHCR_PULL_TOKEN not set — "
                    "Docker Hub rate-limit may apply (~100 pulls/6h)",
                    phase_value,
                )

        elif phase_value == "node_configuration":
            node_yaml = os.environ.get("NODE_YAML", "")
            if not node_yaml or not os.path.isfile(node_yaml):
                raise PhasePreconditionError(f"Phase {phase_value} requires valid NODE_YAML path: {node_yaml}")

        elif phase_value == "certificates":
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

        elif phase_value in ("deploy_services", "deploy_update"):
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

        elif phase_value in ("converge_services", "converge_update"):
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
        elif phase_value == "secrets_update":
            # Same as SECRETS_PROVISION but non-blocking (update mode)
            pass

        elif phase_value in ("node_config_update", "registry_update"):
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


# endregion FUNC_BootstrapState


# region FUNC_load_state
## @purpose  Load BootstrapState from state.json path. Fresh state on missing/corrupt file.
## @io       ⇥ path: Path → ⎋ BootstrapState
## @complexity O(N) where N = number of steps in existing state
## @invariants
##   - Коррапт state.json (JSONDecodeError/KeyError/ValueError) → WARN + fresh state (не fatal)
##   - Phase-key migration: root-level phase keys копируются в steps (migrate_state_to_phases legacy)
def load_state(path: Path) -> BootstrapState:
    """Load bootstrap state from JSON file (fresh state on missing/corrupt)."""
    if not path.exists():
        logger.info("[IMP:7][StateMachine][init] No state file at %s — creating fresh", path)
        return BootstrapState()

    logger.info("[IMP:7][StateMachine][init] Loading state from %s", path)
    try:
        with open(path) as f:
            data = json.load(f)
        # DevPlan 091 Wave B (AC8): load directly from BootstrapPhase keys.
        # state.json now contains only phase keys (system_bootstrap, user_accounts, …).
        state = BootstrapState.from_dict(data)
        # Phase key migration: copy phase keys from root level into steps dict.
        # This handles migrated state.json files where migrate_state_to_phases()
        # wrote phase keys at root level but from_dict only reads data["steps"].
        from core.internal.bootstrap.lifecycle.state_machine import BootstrapPhase

        for pv in BootstrapPhase.ALL_PHASES:
            if pv in data and pv not in state.steps:
                state.steps[pv] = data[pv]
        logger.info(
            "[IMP:8][StateMachine][init] State loaded: mode=%s node=%s current_step=%d",
            state.mode,
            state.node,
            state.current_step,
        )
        return state
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("[IMP:7][StateMachine][init] Corrupt state file %s: %s — creating fresh", path, e)
        return BootstrapState()


# endregion FUNC_load_state


# region FUNC_save_state
## @purpose  Persist state to JSON file atomically (tmp + rename). Creates parent dirs.
## @io       ⇥ state: BootstrapState, path: Path → ⎋ None
## @complexity O(N) where N = number of steps
## @invariants
##   - Атомарная запись: write tmp → replace — коррапт state.json невозможен при crash
##   - OSError → RE-RAISE (fatal) — потеря state недопустима
def save_state(state: BootstrapState, path: Path) -> None:
    """Write state to JSON file atomically (write to tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(state.to_dict(), f, indent=2, ensure_ascii=False)
        tmp_path.replace(path)
        logger.debug("[IMP:6][StateMachine][save] State saved to %s", path)
    except OSError as e:
        logger.error("[IMP:10][StateMachine][save] Failed to save state: %s", e)
        raise


# endregion FUNC_save_state
