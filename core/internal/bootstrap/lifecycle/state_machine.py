#!/usr/bin/env python3
# GREP_SUMMARY: state-machine, bootstrap, lifecycle, node-init, node-update, checkpoint-resume, phase-transitions, state-json, content-hash, BootstrapPhase, phase-dependency-graph, precondition-check
# STRUCTURE: ▶ [BootstrapPhase enum (14)] → ┌StepState + BootstrapState (re-export из state_store)┐ → ◇ precondition_check() → ○ execute_phase() → ◇ statuses {done|done_with_warnings|...} → ⚡ save() → ⎋ compat CLI (lazy cli.py)
# region MODULE_CONTRACT
## @purpose  Explicit state machine for node-lifecycle.sh bootstrap/update process.
##           Manages 14 consolidated phases (φ1-φ13 + φ8.5) via a JSON state file
##           at /var/lib/platform/.bootstrap/state.json (configurable).
##           Each phase is a typed transition with precondition and dependency checks.
##           ЧИСТАЯ ОРКЕСТРАЦИЯ: persistence (state_store.py), I/O-хелперы (helpers/),
##           CLI (cli.py) вынесены (B9 T1/T2, U-08) — state_machine.py ≤ 1200 LOC (гейт T6.2).
## @scope    Python-side of W4-E2 Strangler-Fig decomposition of node-lifecycle.sh (1301 LOC).
##           Handles: state orchestration, content-hash invalidation, checkpoint-resume,
##           phase precondition checks, phase dependency graph, WARN-статусы (done_with_warnings),
##           TOR-conditional skip, error/warning collection, dry-run, force-reset.
##           Business logic extraction → phases.py; I/O → lifecycle/helpers/; CLI → lifecycle/cli.py.
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
##   13. Sub-step resume (execute_grouped_phase) УДАЛЁН (волна 117 D5) — фазы выполняются
##       целиком; идемпотентность через phase-статусы: done_with_warnings ≠ done → перевыполнение
##   14. Зависимости: state_machine → phases (динамический импорт в execute_phase) → helpers;
##       односторонняя (цикл phases↔state_machine устранён, B9 T1)
##   15. BootstrapState/StepState/load_state/save_state re-экспортируются из state_store —
##       публичный контракт пакета (тесты и cli.py не меняют импорты)
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph
##             and precondition checks. Eliminates 8 silent failure propagation points.
##             _phase_dependency_graph replaces implicit sequential ordering with explicit DAG.
##             DevPlan 116 B9 (U-08): SRP-декомпозиция монолита — оркестрация остаётся здесь.
## @changes  2026-07-22 | W4-E2 — Created from node-lifecycle.sh decomposition
##           2026-07-24 | W5.T5.3 — Added HC_DONE_MARKER check in healthcheck step
##           2026-07-25 | DevPlan 071 Rev 2 — Name-based state.json keys, numeric-key backward compat
##           2026-07-30 | T19/T20a/T21 — Shared module extraction (telegram_notifier, docker_auth)
##           2026-07-30 | DevPlan 087 — BootstrapPhase enum (14 values), _phase_dependency_graph,
##           precondition_check(), _execute_phase().
##           Added `--phase` CLI argument for phase-level execution.
##           2026-08-01 | B9 T1/T2 — helpers/, state_store.py, cli.py extraction (2284 → ~950 LOC)
##           2026-08-01 | Волна 117 D5 — execute_grouped_phase удалён (мёртвый код); WARN-семантика
##           done_with_warnings (≠ done); честный current_step
##           2026-08-02 | Волна 118 B1 — step-API удалён (start_step/complete_step/skip_step/fail_step/
##           get_current_step + _is_step_done/_is_step_skipped/_hash_changed/_check_precondition/
##           _check_postcondition/_step_name + StateTransitionError) — 0 callers в core/ + tests/
##           (CLI работает через execute_phase/setup_state, grouped-phases эра B9). R5: hasattr=False.
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import ClassVar

import yaml  # B8 (142 W7): node.yaml — YAML (json.load падал); python3-yaml — платформенная зависимость φ1

from core.internal.bootstrap.lifecycle.state_store import (
    BootstrapState,
    StateCorruptError,
    StepState,
    load_state,
    save_state,
)
from core.internal.shared.content_hash import compute_content_hash as _shared_compute_content_hash

# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


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

# Grouped-phase sub-step resume (execute_grouped_phase) УДАЛЕНО (волна 117 D5):
# · Решение зафиксировано — sub-step resume вне скоупа волны (без нового функционала).
# · Фазы выполняются целиком; идемпотентность обеспечивается phase-статусами
# · (done / done_with_warnings / pending / failed) — WARN-фазы перевыполняются при следующем init.

# ── Phase statuses (волна 117 D5: WARN-семантика) ──────────────────────────
# Статус-константы фаз. done_with_warnings — фаза завершилась с non-fatal issues
# (phase-функция вернула False): НЕ считается done → перевыполняется при следующем init.
PHASE_STATUS_DONE = "done"
PHASE_STATUS_DONE_WITH_WARNINGS = "done_with_warnings"
PHASE_STATUS_PENDING = "pending"
PHASE_STATUS_FAILED = "failed"
PHASE_STATUS_SKIPPED = "skipped"
PHASE_STATUS_RUNNING = "running"

# Фазы, инвалидируемые content-hash'ом входов (T9.3, L-4/B-1): потребляют modules/services
# из node.yaml. Прочие фазы (φ1-φ7, φ9-φ10) от node.yaml modules не зависят — их done не
# сбрасывается hash'ом (легаси-совместимость + отсутствие лишних перевыполнений).
_HASH_INVALIDATED_PHASES = frozenset(
    {
        BootstrapPhase.DEPLOY_SERVICES,  # φ8 deploy-modules (modules/services)
        BootstrapPhase.CONVERGE_SERVICES,  # φ8.5 converge
        BootstrapPhase.REGISTRY_UPDATE,  # φ11 provision/overlays/healthcheck
        BootstrapPhase.DEPLOY_UPDATE,  # φ12 deploy-modules
        BootstrapPhase.CONVERGE_UPDATE,  # φ13 converge
    }
)


def phase_is_done(phase_state) -> bool:
    """Return True if a phase state entry is completed successfully (status == 'done').

    ## @purpose — Единая ПУБЛИЧНАЯ done-проверка для dict- и StepState-представлений (волна 117 D5).
    ##             done_with_warnings НЕ считается done — фаза с non-fatal issues перевыполняется.
    ## @io — ⇥ phase_state: StepState | dict → ⎋ bool
    ## @complexity — O(1)
    ## @invariants
    ##   - dict-представление: status == "done" ИЛИ (done-ключ true при отсутствии status)
    ##   - StepState: status == "done"
    ##   - Любой другой статус (pending/failed/running/done_with_warnings/skipped) → False
    """
    if isinstance(phase_state, dict):
        if phase_state.get("status") == PHASE_STATUS_DONE:
            return True
        # backward-compat: старые state.json могли писать только done:true без status
        return bool(phase_state.get("done", False)) and phase_state.get("status") in (None, PHASE_STATUS_DONE)
    return getattr(phase_state, "status", PHASE_STATUS_PENDING) == PHASE_STATUS_DONE


# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"

# INIT_STEPS / UPDATE_STEPS / *_COUNT removed (DevPlan 091 Wave B, AC8).
# Pre-DevPlan-087 23-step dispatch was consolidated to 14 phases. The numeric-key
# fallback in from_dict() no longer has a caller since __init__ now loads directly
# from BootstrapPhase keys. See state_store.from_dict for the simplified loading path.


# ── Retry Policy (W5-E6 C2) ──
# Единый реестр retry-политик — timeouts.py (DevPlan 117 D34):
#   RETRY_COUNT=2 попыток, RETRY_BACKOFF_EXPONENTIAL_BASE=2 (backoff 2**attempt: 2, 4)
from core.internal.shared.timeouts import RETRY_BACKOFF_EXPONENTIAL_BASE, RETRY_COUNT

RETRYABLE_EXCEPTIONS = (subprocess.TimeoutExpired, FileNotFoundError, OSError)


def _should_retry(exc: Exception, attempt: int) -> bool:
    """Return True if the exception is retryable and attempt < RETRY_COUNT.

    ## @purpose — Exponential-backoff retry policy for transient step failures.
    ## @io — ⇥ exc: caught exception, attempt: current attempt (1-based)
    ##       ⎋ bool: True to retry, False to fail-fast
    ## @complexity — O(1)
    """
    if isinstance(exc, RETRYABLE_EXCEPTIONS) and attempt < RETRY_COUNT:
        backoff = RETRY_BACKOFF_EXPONENTIAL_BASE**attempt
        logger.info(
            "[IMP:8][_should_retry] Retryable exception (attempt %d/%d): %s — backing off %ds",
            attempt,
            RETRY_COUNT,
            exc,
            backoff,
        )
        time.sleep(backoff)
        return True
    return False


# region STATEMACHINE


class StateMachine:
    """State machine for bootstrap/update lifecycle steps.

    ## @purpose — Load/create/save state.json, orchestrate step transitions,
    ##            compute content hashes, validate env, dispatch to step implementations.
    ## @scope — Core of W4-E2 decomposition. Manages state transitions only —
    ##          actual step logic lives in phases.py (14 phase implementations).
    ##          Persistence (state.json I/O) — в lifecycle/state_store.py (B9 T2).
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
    ## @invariants
    ##   - Коррапт state.json → PlatformFatalError (T9.2: явная ошибка; recovery — rm / --force)
    def __init__(self, state_file_path: str = DEFAULT_STATE_FILE) -> None:
        self.state_file = Path(state_file_path)
        self.core_dir: str | None = None
        # Persistence делегируется state_store.load_state (fresh state ТОЛЬКО на missing).
        # T9.2 (DevPlan 136 W9): коррапт → StateCorruptError → PlatformFatalError с инструкцией.
        try:
            self.state = load_state(self.state_file)
        except StateCorruptError as e:
            # ⚠️ TRAP[BUG] · 2026-08-05 · HI · коррапт state.json тихо сбрасывался (L-2/B-2)
            # · Symptom: повреждённый state.json → молчаливый fresh state → фазы перевыполнялись
            #   или checkpoint'ы терялись без следа (DevPlan 136 W9 T9.2).
            # · Fix: StateCorruptError → PlatformFatalError; --force (cli.main) удаляет файл.
            logger.critical("[IMP:10][StateMachine][init] %s", e)
            raise PlatformFatalError(str(e)) from e

    # endregion FUNC___init__

    # region FUNC_save
    ## @purpose — Persist current state to JSON file atomically (delegates to state_store).
    ## @io — ⇥ None → ⎋ None (side-effect: writes state.json)
    ## @complexity — O(N) where N = number of steps
    def save(self) -> None:
        """Write state to JSON file atomically (write to tmp then rename)."""
        save_state(self.state, self.state_file)

    # endregion FUNC_save

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

    # region FUNC__phase_input_hash
    ## @purpose — Compute content hash of phase-relevant INPUTS (DevPlan 136 W9 T9.3, L-4/B-1):
    ##            modules + services из node.yaml (релевантные поля, НЕ весь файл — риск §9 meta)
    ##            + lifecycle code (state_machine.py) + phase-агрегатор (phases/__init__.py),
    ##            чтобы смена кода платформы тоже инвалидировала done-фазу.
    ##            Hash сохраняется в StepState.hash при успехе фазы (см. cli._mark_phase_success).
    ## @io — ⇥ phase_value: str → ⎋ str (SHA256 hexdigest)
    ## @complexity O(N) где N = размер релевантных полей node.yaml
    ## @invariants
    ##   - ТОЛЬКО релевантные поля (modules, services) — правка нерелевантного поля (например,
    ##     ssh_authorized_keys) НЕ инвалидирует deploy-фазы (risk §9: «hash только по релевантным полям»)
    ##   - node.yaml отсутствует/битый → детерминированный «no-node-yaml» сегмент (не падает)
    ##   - Включает state_machine.py + phases/__init__.py: обновление платформы перевыполняет
    ##     deploy-фазы (B-1: update-фазы инвалидируются hash'ом)
    def _phase_input_hash(self, phase_value: str) -> str:
        """Hash of phase-relevant inputs (node.yaml modules/services + lifecycle code)."""
        import hashlib

        hasher = hashlib.sha256()
        hasher.update(phase_value.encode("utf-8"))
        node_yaml = os.environ.get("NODE_YAML", "")
        if node_yaml and os.path.isfile(node_yaml):
            try:
                # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B8 (142 W7): json.loads на YAML node.yaml
                # · Symptom: «Cannot parse node.yaml» на КАЖДОЙ фазе + сломанный content-hash —
                # ·   hash всегда = «node-yaml-unparseable» → done-фазы перевыполнялись/нет (1,2 циклы 141).
                # · Root: node.yaml — YAML, json.load падает на первом non-JSON токене (node:).
                # · Fix: yaml.safe_load (PyYAML — платформенная зависимость, python3-yaml в φ1 apt).
                # · Prevention: node.yaml читается ТОЛЬКО как YAML (канон NodeYaml фасада).
                with open(node_yaml, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                relevant = {"modules": data.get("modules", {}), "services": data.get("services", {})}
                hasher.update(json.dumps(relevant, sort_keys=True, default=str).encode("utf-8"))
            except (OSError, yaml.YAMLError, json.JSONDecodeError) as e:
                # Best-effort: битый node.yaml — детерминированный fallback (не фатально на hash-стадии)
                logger.warning("[IMP:7][_phase_input_hash] Cannot parse %s: %s", node_yaml, e)
                hasher.update(b"node-yaml-unparseable")
        else:
            hasher.update(b"no-node-yaml")
        # Код-инвалидация: состояние lifecycle-оркестратора (изменение платформы → re-run)
        code_path = os.path.abspath(__file__)
        try:
            with open(code_path, "rb") as f:
                hasher.update(f.read())
        except OSError:
            hasher.update(b"code-missing")
        digest = hasher.hexdigest()
        logger.debug("[IMP:6][_phase_input_hash] Phase %s input hash: %s", phase_value, digest[:12])
        return digest

    # endregion FUNC__phase_input_hash

    # region FUNC_phase_needs_rerun
    ## @purpose — True если фаза отмечена done, но её входы изменились (hash mismatch) —
    ##            content-hash инвалидация (T9.3, L-4/B-1). Участвуют deploy/converge-фазы;
    ##            прочие фазы (φ1-φ7, φ9-φ10) не зависят от modules/services → False (backward-compat:
    ##            legacy done-фазы без hash'а НЕ перевыполняются).
    ## @io — ⇥ phase_value: str → ⎋ bool
    ## @complexity O(N) где N = hash входов
    ## @invariants
    ##   - Только HASH_INVALIDATED_PHASES: deploy_services, deploy_update, registry_update,
    ##     converge_services, converge_update — фазы, потребляющие modules/services из node.yaml
    ##   - StepState без сохранённого hash (legacy) → False (done сохраняется — обратная совместимость)
    ##   - mismatch → True (cli перевыполнит фазу, сбросив статус в pending)
    def phase_needs_rerun(self, phase_value: str) -> bool:
        """Return True if a done phase must re-run because its inputs changed (T9.3)."""
        if phase_value not in _HASH_INVALIDATED_PHASES:
            return False
        entry = self.state.steps.get(phase_value)
        if entry is None or not phase_is_done(entry):
            return False
        stored_hash = getattr(entry, "hash", None) if not isinstance(entry, dict) else entry.get("hash")
        if not stored_hash:
            return False  # legacy: hash не сохранялся → done сохраняется
        current = self._phase_input_hash(phase_value)
        changed = current != stored_hash
        if changed:
            logger.info(
                "[IMP:9][phase_needs_rerun] Phase %s inputs changed (hash %s → %s) — re-run required (T9.3)",
                phase_value,
                stored_hash[:12],
                current[:12],
            )
        return changed

    # endregion FUNC_phase_needs_rerun

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
            # done_with_warnings НЕ считается done (волна 117 D5) — WARN-фаза не удовлетворяет
            # зависимость и блокирует downstream-фазы до успешного перевыполнения
            if not phase_is_done(phase_state):
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

        # Execute (T9.11, B-3): _should_retry вокруг phase_func — транзиентные сбои
        # (TimeoutExpired/OSError/FileNotFoundError) ретраятся RETRY_COUNT=2 с экспоненциальным
        # backoff. ВАЖНО: ретраится ТОЛЬКО raise-путь (фаза НЕ вернула bool) — фаза, вернувшая
        # False (non-fatal issues), НЕ ретраится (это WARN-семантика done_with_warnings, волна 117 D5).
        core_dir = self.core_dir or os.environ.get("CORE_DIR", str(platform_remote_base() / "core"))
        node_name = os.environ.get("NODE_NAME", "")
        node_yaml = os.environ.get("NODE_YAML", "")

        attempt = 1
        while True:
            try:
                result = phase_func(core_dir, node_name, node_yaml)
                break
            except RETRYABLE_EXCEPTIONS as exc:
                if not _should_retry(exc, attempt):
                    raise
                attempt += 1
        logger.info(
            "[IMP:9][execute_phase] Phase %s completed: %s",
            phase_value,
            "success" if result else "with warnings",
        )
        return result

    # endregion FUNC_execute_phase

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

    # region FUNC_setup_state
    ## @purpose — Initialize state for a run: set mode, node, create phase entries.
    ## @io — ⇥ mode: str, node: Optional[str] → ⎋ None
    ## @complexity — O(N) where N = number of phases
    def setup_state(self, mode: str, node: str | None = None) -> None:
        """Initialize state for a new run with phase-based keys.

        Sets mode, node, creates MISSING phase entries as pending (existing preserved).
        current_step сбрасывается в 0 только здесь (fresh/mode-change); в run-циклах (cli.py)
        он честно обновляется при успехе фазы (волна 117 D5) — больше НЕ всегда 0.
        """
        # Волна 117 D5: TRAP[BUG] (2026-07-31, «current_step всегда 0») снят — root-причина
        # устранена: (а) setdefault-семантика сохранена (done остаётся done), (б) cli.py
        # run_init/run_update обновляют current_step при успешном выполнении фазы.
        # ⚠️ TRAP[BUG] 2026-08-03 · node switch на той же VPS: state.json от ДРУГОЙ ноды
        # · Symptom: прод-бустрап tronyx-vps на VPS после e2e (test-e2e) — ВСЕ 9 фаз
        #   «already done — skipping» (state.json: node=test-e2e) → ложный no-op bootstrap.
        # · Root: setup_state сохранял existing (setdefault) без проверки node identity.
        # · Fix: node mismatch → сброс фаз/ошибок (fresh lifecycle для новой ноды);
        #   идемпотентность для ТОЙ ЖЕ ноды сохранена (совпадение node → existing preserved).
        self.state.mode = mode
        if node and self.state.node and self.state.node != node:
            logger.warning(
                "[IMP:7][StateMachine][setup_state] Node switch %s → %s — resetting phase state",
                self.state.node,
                node,
            )
            self.state.steps = {}
            self.state.errors = []
            self.state.warnings = []
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


# ── Compat-заглушка CLI (DevPlan 116 B9 T1) ──
# Прямые запуски `python3 lifecycle/state_machine.py --mode ...` продолжают работать:
# CLI загружается лениво (только при прямом запуске скрипта) — без цикла импортов
# (cli.py импортирует state_machine; state_machine НЕ импортирует cli на уровне модуля).
if __name__ == "__main__":
    from core.internal.bootstrap.lifecycle.cli import main

    sys.exit(main())
