#!/usr/bin/env python3
# GREP_SUMMARY: state-machine, bootstrap, lifecycle, node-init, node-update, checkpoint-resume, phase-transitions, state-json, content-hash, BootstrapPhase, phase-dependency-graph, precondition-check, PhaseContext, PHASE_DISPATCH
# STRUCTURE: ▶ [BootstrapPhase enum (14)] → ┌StepState + BootstrapState (re-export из state_store)┐ → ◇ PHASE_DISPATCH registry (14 фаз, статический импорт) → ○ execute_phase(phase, ctx) → ◇ statuses {done|done_with_warnings|...} → ⚡ save() → ⎋ compat CLI (lazy cli.py)
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
##           Business logic extraction → phases/ (включая прекондишены — phases/preconditions.py,
##           план 170 W5-C1); I/O → lifecycle/helpers/; CLI → lifecycle/cli.py.
## @invariants
##   1. State file is at /var/lib/platform/.bootstrap/state.json (configurable via --state-file)
##   2. Phase subprocess timeouts задаются вызываемыми фазами через shared/timeouts
##      (канон SoT); state_machine сам subprocess не исполняет (фазы — lifecycle/phases/*.py)
##   3. Non-fatal failures log WARN and continue — errors list collected for final audit
##   4. Content hash = sha256(node.yaml modules/services + state_machine.py + phases/*.py байты)
##   5. --dry-run prints plan and exits 0 BEFORE any mutations
##   6. --force clears all state (rm state file)
##   7. --resume loads existing state and continues from last checkpoint
##   8. TOR_ENABLED=false → tor_proxy sub-step is skipped (not failed)
##   9. State file format: {mode, node, current_step, phases: {str: PhaseState}, errors, warnings}
##   10. CLI args or env vars for: NODE_NAME, NODE_YAML, PLATFORM_OWNER_KEY, PLATFORM_CI_DEPLOY_KEY
##   11. Phase dependency graph enforces execution order: φ2 ← φ1, φ4 ← φ3, φ6 ← φ4, φ8 ← φ4+φ6+φ7
##   12. precondition_check() verifies intra-phase conditions BEFORE execution
##   13. Sub-step resume отсутствует — фазы выполняются целиком; идемпотентность через
##       phase-статусы: done_with_warnings ≠ done → перевыполнение САМОЙ warn-фазы,
##       НО dependency-gate удовлетворяется {done, done_with_warnings} (drill C2)
##   14. Зависимости: state_machine → phases (СТАТИЧЕСКИЙ импорт — реестр PHASE_DISPATCH,
##       план 170 W5-C3) → helpers; односторонняя (цикл phases↔state_machine устранён, B9 T1)
##   15. BootstrapState/StepState/load_state/save_state re-экспортируются из state_store —
##       публичный контракт пакета (тесты и cli.py не меняют импорты)
##   16. PhaseDependencyError/PhasePreconditionError — из leaf exceptions.py (W10-design п.1);
##       re-export отсюда сохраняет контракт импортёров (cli.py/тесты)
##   17. Capability-сеты ENV_AWARE_PHASES/FACTS_AWARE_PHASES живут в phases/__init__.py
##       (рядом с сигнатурами); state_machine импортирует с приватным алиасом (W5-C3)
##   18. execute_phase: параметры → PhaseContext dataclass (ctx= — новый контракт);
##       старые kwargs (env/facts/helpers/sleep_fn/phase_func_override) СОХРАНЕНЫ
##       (обратная совместимость cli.py и тестов — правки вызовов НЕ требуются)
## @rationale  DevPlan 087: Consolidate 32+ steps → 14 phases with explicit dependency graph
##             and precondition checks. Eliminates 8 silent failure propagation points.
##             _phase_dependency_graph replaces implicit sequential ordering with explicit DAG.
##             DevPlan 116 B9 (U-08): SRP-декомпозиция монолита — оркестрация остаётся здесь.
##             План 170 W5-C3: execute_phase 152 LOC/9 params → PhaseDispatcher
##             (статический реестр PHASE_DISPATCH + PhaseContext + _call_with_retry) —
##             фаза-диспетчер ≤40 LOC; ретрай-цикл извлечён в helper (семантика 1:1).
## @changes  2026-07-22 | W4-E2 — Created from node-lifecycle.sh decomposition
## @changes  2026-08-27 | drill C2 (P1) — dependency-gate: статус-набор удовлетворения
##            зависимости = {done, done_with_warnings} В ОБОИХ режимах (init/update);
##            phase_satisfies_dependency() — отдельный предикат; phase_is_done остаётся
##            строгим (re-run/exit-code/strict-init T9); failed/pending/skipped НЕ удовлетворяют
## @changes  2026-08-13 | DevPlan 160 E3 — StateMachine/execute_phase +facts: EnvironmentFacts
##            (DI is_root/path_isfile для прекондишенов и facts-aware фаз)
##           2026-07-24 | W5.T5.3 — Added HC_DONE_MARKER check in healthcheck step
##           2026-07-25 | DevPlan 071 Rev 2 — Name-based state.json keys
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
##           2026-08-15 | План 170 W5-C1/C3 — exceptions → leaf exceptions.py (re-export);
##           прекондишены → phases/preconditions.py; capability-сеты → phases/__init__.py;
##           execute_phase → PhaseDispatcher (PHASE_DISPATCH + PhaseContext + _call_with_retry);
##           статический импорт фаз (цикл state_machine→phases разорван — ignore-ребро удалено)
##           2026-08-16 | DevPlan 177 W3.1 — _call_with_retry → делегат shared/retry.py;
##           _should_retry удалён (retryable-предикат + backoff — в shared.retry, 1:1 семантика)
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

import yaml  # B8 (142 W7): node.yaml — YAML (json.load падал); python3-yaml — платформенная зависимость φ1

# W10-design п.1 / W5-C1: доменные ошибки — leaf exceptions.py (цикл state_store↔state_machine разорван).
# re-export: импортёры PhaseDependencyError/PhasePreconditionError из state_machine НЕ меняют импорты.
from core.internal.bootstrap.lifecycle.exceptions import (
    PhaseDependencyError,
    PhasePreconditionError,  # ruff: ignore[F401] / pyright: ignore[reportUnusedImport] — re-export для импортёров (cli.py/тесты, W10-design п.1)
)
from core.internal.bootstrap.lifecycle.phases import (
    ENV_AWARE_PHASES as _ENV_AWARE_PHASES,  # W5-C3: capability-сеты переехали в phases/__init__.py
)
from core.internal.bootstrap.lifecycle.phases import (
    FACTS_AWARE_PHASES as _FACTS_AWARE_PHASES,
)
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
from core.internal.bootstrap.lifecycle.state_store import (
    BootstrapState,
    StateCorruptError,
    StepState,
    load_state,
    save_state,
)

# Канонический platform base — shared/deploy_paths (литерал /opt/platform не используется)
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.env_facts import EnvironmentFacts  # E3 (160): facts DI тип (TYPE-only)
from core.internal.shared.exceptions import PlatformFatalError

logger = logging.getLogger(__name__)


# ── QA R2 (DevPlan 14 T2.B): run-start timestamp — хранилище вынесено в leaf
# lifecycle/run_context.py (разрыв цикла phases/docker ↔ state_machine, import-linter);
# здесь — re-export API для существующих потребителей (тесты, cli).
from core.internal.bootstrap.lifecycle.run_context import (  # ruff: ignore[F401] / pyright: ignore[reportUnusedImport] — re-export для импортёров API run-start
    get_run_start_ts,
    reset_run_start_ts,
    set_run_start_ts,
)


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
    INIT_PHASES = frozenset({
        SYSTEM_BOOTSTRAP,
        USER_ACCOUNTS,
        PLATFORM_SETUP,
        SECRETS_PROVISION,
        NODE_CONFIGURATION,
        REGISTRY_AUTH,
        CERTIFICATES,
        DEPLOY_SERVICES,
        CONVERGE_SERVICES,
    })

    UPDATE_PHASES = frozenset({
        SECRETS_UPDATE,
        NODE_CONFIG_UPDATE,
        REGISTRY_UPDATE,
        DEPLOY_UPDATE,
        CONVERGE_UPDATE,
    })

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

# Sub-step resume отсутствует: фазы выполняются целиком; идемпотентность обеспечивается
# phase-статусами (done / done_with_warnings / pending / failed) — WARN-фазы
# перевыполняются при следующем init.

# ── Phase statuses (волна 117 D5: WARN-семантика) ──────────────────────────
# Статус-константы фаз. done_with_warnings — фаза завершилась с non-fatal issues
# (phase-функция вернула False): НЕ считается done → перевыполняется при следующем init.
PHASE_STATUS_DONE = "done"
PHASE_STATUS_DONE_WITH_WARNINGS = "done_with_warnings"
PHASE_STATUS_PENDING = "pending"
PHASE_STATUS_FAILED = "failed"
PHASE_STATUS_SKIPPED = "skipped"
PHASE_STATUS_RUNNING = "running"

# ── Dependency-satisfaction status set (drill C2, 2026-08-27) ───────────────
# Статус-набор УДОВЛЕТВОРЕНИЯ dependency-гейта: done ИЛИ done_with_warnings.
# done_with_warnings-фаза перевыполняется САМА (phase_is_done strict — re-run/exit-code),
# НО УДОВЛЕТВОРЯЕТ зависимости downstream: один некритичный warning не должен навсегда
# рвать цепочку update (P1 2026-08-27: φ11 registry_update done_with_warnings → φ12
# deploy_update «requires prerequisite phase(s): registry_update» до ручного сброса state).
PHASE_STATUS_SATISFIES_DEPENDENCY = frozenset({PHASE_STATUS_DONE, PHASE_STATUS_DONE_WITH_WARNINGS})

# Фазы, инвалидируемые content-hash'ом входов (T9.3, L-4/B-1): потребляют modules/services
# из node.yaml. Прочие фазы (φ1-φ7, φ9-φ10) от node.yaml modules не зависят — их done не
# сбрасывается hash'ом (легаси-совместимость + отсутствие лишних перевыполнений).
_HASH_INVALIDATED_PHASES = frozenset({
    BootstrapPhase.DEPLOY_SERVICES,  # φ8 deploy-modules (modules/services)
    BootstrapPhase.CONVERGE_SERVICES,  # φ8.5 converge
    BootstrapPhase.REGISTRY_UPDATE,  # φ11 provision/overlays/healthcheck
    BootstrapPhase.DEPLOY_UPDATE,  # φ12 deploy-modules
    BootstrapPhase.CONVERGE_UPDATE,  # φ13 converge
})


# ── Фазовый диспетчер (план 170 W5-C3: PhaseDispatcher) ──────────────────────────────
# Реестр phase_value → phase_func. СТАТИЧЕСКИЙ импорт фаз (module-level, из phases/) —
# динамический import внутри execute_phase удалён: цикл state_machine → phases
# разорван (phases НЕ импортирует state_machine), ignore-ребро .importlinter:155 удалено.
# Capability-сеты _ENV_AWARE_PHASES/_FACTS_AWARE_PHASES живут в phases/__init__.py
# (рядом с сигнатурами фаз) — state_machine импортирует их (см. импорты выше).
PHASE_DISPATCH: dict[str, Callable[..., object]] = {
    BootstrapPhase.SYSTEM_BOOTSTRAP: phase_system_bootstrap,  # φ1
    BootstrapPhase.USER_ACCOUNTS: phase_user_accounts,  # φ2
    BootstrapPhase.PLATFORM_SETUP: phase_platform_setup,  # φ3
    BootstrapPhase.SECRETS_PROVISION: phase_secrets_provision,  # φ4
    BootstrapPhase.NODE_CONFIGURATION: phase_node_configuration,  # φ5
    BootstrapPhase.REGISTRY_AUTH: phase_registry_auth,  # φ6
    BootstrapPhase.CERTIFICATES: phase_certificates,  # φ7
    BootstrapPhase.DEPLOY_SERVICES: phase_deploy_services,  # φ8
    BootstrapPhase.CONVERGE_SERVICES: phase_converge_services,  # φ8.5
    BootstrapPhase.SECRETS_UPDATE: phase_secrets_update,  # φ9
    BootstrapPhase.NODE_CONFIG_UPDATE: phase_node_config_update,  # φ10
    BootstrapPhase.REGISTRY_UPDATE: phase_registry_update,  # φ11
    BootstrapPhase.DEPLOY_UPDATE: phase_deploy_update,  # φ12
    BootstrapPhase.CONVERGE_UPDATE: phase_converge_update,  # φ13
}


# region CLASS_PhaseContext
## @purpose  Контекст исполнения фазы (план 170 W5-C3): 9 параметров execute_phase →
##           dataclass. Диспетчер собирает kwargs по capability-сетам из контекста.
## @io       ⇥ core_dir, node_name, node_yaml + DI-поля (env/facts/helpers) → ⎋ PhaseContext
## @complexity O(1)
## @invariants
##   - env — MERGE-источник {**os.environ, **env} (см. _resolve_phase_context)
##   - facts/helpers — None → фаза сама берёт default (канон W-H/E3: DI-шов опционален)
@dataclass(frozen=True)
class PhaseContext:
    """DI-контекст исполнения фазы (core_dir/node/env/facts/helpers)."""

    core_dir: str
    node_name: str
    node_yaml: str
    env: Mapping[str, str] | None = None  # merged-источник (os.environ + override) для env-aware фаз
    facts: EnvironmentFacts | None = None  # facts-дикт (None → фаза берёт default_env_facts)
    system_helpers: object | None = None  # φ1 helpers= / φ3 sys_helpers= (W-H namespace-DI)
    users_helpers: object | None = None  # φ2 users_helpers=
    val_helpers: object | None = None  # φ3 val_helpers=


# endregion CLASS_PhaseContext


# region FUNC__phase_kwargs
## @purpose  Сборка kwargs фазы по capability-сетам (W5-C3): env-aware фазы получают
##           env-дикт; facts-aware — facts= ТОЛЬКО когда facts предоставлен (иначе фаза
##           сама берёт default_env_facts — поведение неизменно); helper-неймспейсы —
##           по фазам (φ1 helpers=, φ3 sys_helpers=/val_helpers=, φ2 users_helpers=).
## @io       ⇥ phase_value: str, pctx: PhaseContext → ⎋ dict[str, Any]
## @complexity O(1) — сеты + несколько сравнений
## @invariants
##   - Точная эквивалентность прежней kwargs-сборке execute_phase (W4e/E3/W-H)
##   - Типы гетерогенны (env-дикт/facts/helpers-неймспейсы) — Any в сигнатуре
def _phase_kwargs(phase_value: str, pctx: PhaseContext) -> dict[str, object]:
    """Build phase kwargs from capability sets (env/facts/helpers DI)."""
    kwargs: dict[str, object] = {}
    if phase_value in _ENV_AWARE_PHASES:
        kwargs["env"] = pctx.env
    if pctx.facts is not None and phase_value in _FACTS_AWARE_PHASES:
        kwargs["facts"] = pctx.facts
    # W-H (DevPlan 163): helper-namespace инъекция в фазы (None → каноны)
    if pctx.system_helpers is not None and phase_value in {
        BootstrapPhase.SYSTEM_BOOTSTRAP,
        BootstrapPhase.PLATFORM_SETUP,
    }:
        kwargs["helpers" if phase_value == BootstrapPhase.SYSTEM_BOOTSTRAP else "sys_helpers"] = pctx.system_helpers
    if pctx.val_helpers is not None and phase_value == BootstrapPhase.PLATFORM_SETUP:
        kwargs["val_helpers"] = pctx.val_helpers
    if pctx.users_helpers is not None and phase_value == BootstrapPhase.USER_ACCOUNTS:
        kwargs["users_helpers"] = pctx.users_helpers
    return kwargs


# endregion FUNC__phase_kwargs


# region FUNC__call_with_retry
## @purpose  Исполнение phase_func с retry на транзиентные сбои (T9.11, B-3; извлечено
##           из execute_phase, план 170 W5-C3): RETRY_COUNT=2 попыток + экспоненциальный
##           backoff. Тонкий делегат ЕДИНОГО retry-цикла shared/retry.py (DevPlan 177 W3.1):
##           _should_retry удалён — retryable-предикат (isinstance RETRYABLE_EXCEPTIONS)
##           и backoff-логика живут в shared.retry. Ретраится ТОЛЬКО raise-путь (фаза,
##           вернувшая False — WARN done_with_warnings, НЕ ретраится).
## @io       ⇥ phase_func: Callable, args: (core_dir, node_name, node_yaml), kwargs: Mapping,
##              sleep_fn: Callable | None (DI, W-H — backoff-sleep; None = time.sleep)
##           → ⎋ object (результат фазы: bool)
## @complexity O(R) — R = число попыток (≤ RETRY_COUNT+1)
def _call_with_retry(
    phase_func: Callable[..., object],
    args: tuple[str, str, str],
    kwargs: Mapping[str, object],
    *,
    sleep_fn: Callable[[float], None] | None = None,
) -> object:
    """Execute phase_func with retry on transient failures (T9.11, B-3) via shared.retry."""
    # 177 W3.1: exception-mode — исключения RETRYABLE_EXCEPTIONS ретраятся; исчерпание →
    # последний exception re-raise (fail-fast, T9.11); backoff [2**1]=[2] (W5-E6 C2 канон).
    return _shared_retry(
        lambda: phase_func(*args, **kwargs),
        attempts=RETRY_COUNT,  # RETRY_COUNT=2 — общее число попыток (2 попытки, 1 ретрай)
        backoff_seconds=_shared_exponential_backoff(
            retries=RETRY_COUNT - 1,
            base=RETRY_BACKOFF_EXPONENTIAL_BASE,
        ),
        retryable=lambda exc: isinstance(exc, RETRYABLE_EXCEPTIONS),
        sleep_fn=sleep_fn,
        exception_mode=True,
    )


# endregion FUNC__call_with_retry


def phase_is_done(phase_state: StepState | dict[str, object] | None) -> bool:
    """Return True if a phase state entry is completed successfully (status == 'done').

    ## @purpose — Единая ПУБЛИЧНАЯ done-проверка для dict- и StepState-представлений (волна 117 D5).
    ##             done_with_warnings НЕ считается done — фаза с non-fatal issues перевыполняется.
    ##             СТРОГАЯ done-семантика ДЛЯ re-run/exit-code (WARN-перевыполнение, strict-init T9,
    ##             preflight all_done); для dependency-гейта downstream используйте
    ##             phase_satisfies_dependency ({done, done_with_warnings}, drill C2).
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
        # dict-форма (raw) может нести done:true без status
        return bool(phase_state.get("done", False)) and phase_state.get("status") in {None, PHASE_STATUS_DONE}
    return getattr(phase_state, "status", PHASE_STATUS_PENDING) == PHASE_STATUS_DONE


def phase_satisfies_dependency(phase_state: StepState | dict[str, object] | None) -> bool:
    """Return True if a phase state satisfies prerequisites of downstream phases.

    ## @purpose — Dependency-gate предикат (drill C2 fix, 2026-08-27): статус-набор
    ##             {done, done_with_warnings} УДОВЛЕТВОРЯЕТ dependency-gate В ОБОИХ режимах
    ##             (init/update). Отличается от phase_is_done (строгий done — re-run/exit-code):
    ##             done_with_warnings-фаза перевыполняется САМА, но НЕ блокирует downstream —
    ##             иначе один некритичный warning навсегда рвёт цепочку update (P1: φ11
    ##             registry_update done_with_warnings → φ12 deploy_update заблокирован).
    ## @io — ⇥ phase_state: StepState | dict → ⎋ bool
    ## @complexity — O(1)
    ## @invariants
    ##   - status ∈ {done, done_with_warnings} → True (dict и StepState, оба режима)
    ##   - failed/pending/skipped/running → False (guard: незавершённые НЕ удовлетворяют)
    ##   - legacy dict-форма (state.json до StepState): done:true без status → True
    ##   - phase_is_done остаётся СТРОГИМ (done only) — WARN-перевыполнение + strict-init exit
    """
    if isinstance(phase_state, dict):
        status = phase_state.get("status")
        if status in PHASE_STATUS_SATISFIES_DEPENDENCY:
            return True
        # legacy dict-форма: done:true без status (state.json до StepState-миграции)
        return bool(phase_state.get("done", False)) and status in {None, PHASE_STATUS_DONE}
    return getattr(phase_state, "status", PHASE_STATUS_PENDING) in PHASE_STATUS_SATISFIES_DEPENDENCY


# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_STATE_FILE = "/var/lib/platform/.bootstrap/state.json"

# INIT_STEPS / UPDATE_STEPS / *_COUNT отсутствуют: 23-step dispatch консолидирован в 14 фаз
# (name-based step-ключи). См. state_store.from_dict.


# ── Retry Policy (W5-E6 C2 + 177 W3.1) ──
# Единый реестр retry-политик — timeouts.py (DevPlan 117 D34):
#   RETRY_COUNT=2 попыток, RETRY_BACKOFF_EXPONENTIAL_BASE=2 (backoff 2**attempt: 2, 4)
# Единый retry-цикл — shared/retry.py (DevPlan 177 W3.1): _should_retry удалён —
# retryable-предикат (isinstance RETRYABLE_EXCEPTIONS) и backoff-логика переехали туда.
from core.internal.shared.retry import exponential_backoff as _shared_exponential_backoff
from core.internal.shared.retry import retry as _shared_retry
from core.internal.shared.timeouts import RETRY_BACKOFF_EXPONENTIAL_BASE, RETRY_COUNT

# Транзиентные исключения шагов bootstrap (T9.11, B-3): retryable-предикат для shared.retry
RETRYABLE_EXCEPTIONS = (subprocess.TimeoutExpired, FileNotFoundError, OSError)


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
    ##   - Non-fatal failures (WARN) do not advance current_step
    ##   - Content hash = node.yaml modules/services + state_machine.py + lifecycle/phases/*.py
    ##   - Step index 0 = not started; step N = last successfully completed step
    ## @complexity — O(1) per transition; O(N) for full init/update run
    """

    # region FUNC___init__
    ## @purpose — Initialize state machine: load existing state or create new.
    ## @io — ⇥ state_file_path: Path to JSON state file, env: Mapping | None (DI),
    ##          facts: EnvironmentFacts | None (DI, DevPlan 160 E3) → ⎋ None
    ## @complexity — O(N) where N = number of steps in existing state
    ## @invariants
    ##   - Коррапт state.json → PlatformFatalError (T9.2: явная ошибка; recovery — rm / --force)
    ##   - env= Mapping — override env-чтений execute_phase/_phase_input_hash (DevPlan 160 E2,
    ##     W4e DI); None = os.environ (ленивый default — поведение по умолчанию неизменно)
    ##   - facts= EnvironmentFacts — override is_root/path_isfile для фаз+прекондишенов (E3);
    ##     None = default_env_facts (поведение по умолчанию неизменно)
    def __init__(
        self,
        state_file_path: str = DEFAULT_STATE_FILE,
        *,
        env: Mapping[str, str] | None = None,
        facts: EnvironmentFacts | None = None,
        system_helpers: object | None = None,
        users_helpers: object | None = None,
        val_helpers: object | None = None,
    ) -> None:
        self.state_file = Path(state_file_path)
        self.core_dir: str | None = None
        # W4e (DevPlan 160 E2): env-дикт фаз (TOR_ENABLED/SECRETS_ENV_FILE/NODE_YAML/...) —
        # хранится в инстансе и прокидывается в execute_phase/фазы. None = os.environ.
        self._env: Mapping[str, str] | None = env
        # E3 (DevPlan 160): facts-дикт фаз (is_root/path_isfile) — прокидывается в
        # precondition_check + facts-aware фазы. None = default_env_facts.
        self._facts: EnvironmentFacts | None = facts
        # W-H (DevPlan 163): helper-namespace инъекция (None → канонические модули) —
        # flow-тесты передают fake-неймспейсы (0 патчей helpers).
        self._system_helpers: object | None = system_helpers
        self._users_helpers: object | None = users_helpers
        self._val_helpers: object | None = val_helpers
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

    # region FUNC__phase_input_hash
    ## @purpose — Compute content hash of phase-relevant INPUTS (DevPlan 136 W9 T9.3, L-4/B-1):
    ##            modules + services из node.yaml (релевантные поля, НЕ весь файл — риск §9 meta)
    ##            + lifecycle code (state_machine.py) + код фаз (lifecycle/phases/*.py, AI-0038),
    ##            чтобы смена кода платформы тоже инвалидировала done-фазу.
    ##            Hash сохраняется в StepState.hash при успехе фазы (см. cli._mark_phase_success).
    ## @io       ⇥ phase_value: str, env: Mapping | None (DI, DevPlan 160 E2),
    ##              phases_dir: Path | str | None (DI, AI-0038 тесты; None → lifecycle/phases рядом
    ##              с __file__) → ⎋ str (SHA256 hexdigest)
    ## @complexity O(N + P*F) где N = размер релевантных полей node.yaml, P*F = байты phases/*.py
    ## @invariants
    ##   - ТОЛЬКО релевантные поля (modules, services) — правка нерелевантного поля (например,
    ##     ssh_authorized_keys) НЕ инвалидирует deploy-фазы (risk §9: «hash только по релевантным полям»)
    ##   - node.yaml отсутствует/битый → детерминированный «no-node-yaml» сегмент (не падает)
    ##   - Включает state_machine.py + ВСЕ lifecycle/phases/*.py (sorted, байты): обновление
    ##     платформы или правка кода любой фазы перевыполняет deploy-фазы (B-1 + AI-0038)
    @staticmethod
    def _phase_input_hash(
        phase_value: str,
        *,
        env: Mapping[str, str] | None = None,
        phases_dir: Path | str | None = None,
    ) -> str:
        """Hash of phase-relevant inputs (node.yaml modules/services + lifecycle code)."""
        import hashlib

        source: Mapping[str, str] = os.environ if env is None else env
        hasher = hashlib.sha256()
        hasher.update(phase_value.encode("utf-8"))
        node_yaml = source.get("NODE_YAML", "")
        if node_yaml and os.path.isfile(node_yaml):
            try:
                # ⚠️ TRAP[BUG] · 2026-08-06 · HI · B8 (142 W7): json.loads на YAML node.yaml
                # · Symptom: «Cannot parse node.yaml» на КАЖДОЙ фазе + сломанный content-hash —
                # ·   hash всегда = «node-yaml-unparseable» → done-фазы перевыполнялись/нет (1,2 циклы 141).
                # · Root: node.yaml — YAML, json.load падает на первом non-JSON токене (node:).
                # · Fix: yaml.safe_load (PyYAML — платформенная зависимость, python3-yaml в φ1 apt).
                # · Prevention: node.yaml читается ТОЛЬКО как YAML (канон NodeYaml фасада).
                with Path(node_yaml).open(encoding="utf-8") as f:
                    data = cast(
                        "dict[str, object]", yaml.safe_load(f) or {}
                    )  # W11-G3: yaml.safe_load → Any; YAML-граница node.yaml
                relevant = {"modules": data.get("modules", {}), "services": data.get("services", {})}
                hasher.update(json.dumps(relevant, sort_keys=True, default=str).encode("utf-8"))
            except (OSError, yaml.YAMLError, json.JSONDecodeError) as e:
                # Best-effort: битый node.yaml — детерминированный fallback (не фатально на hash-стадии)
                logger.warning("[IMP:7][_phase_input_hash] Cannot parse %s: %s", node_yaml, e)
                hasher.update(b"node-yaml-unparseable")
        else:
            hasher.update(b"no-node-yaml")
        # Код-инвалидация: состояние lifecycle-оркестратора (изменение платформы → re-run)
        code_path = Path(__file__).resolve()
        try:
            with Path(code_path).open("rb") as f:
                hasher.update(f.read())
        except OSError:
            hasher.update(b"code-missing")
        # Код-инвалидация фаз (AI-0038): байты lifecycle/phases/*.py в отсортированном порядке —
        # правка кода ЛЮБОЙ фазы меняет hash → done-фаза перевыполняется (bytes, не mtime:
        # git-checkout выравнивает mtime — sha256 детерминирован и дёшев на 6-10 файлах)
        resolved_phases_dir = Path(phases_dir) if phases_dir is not None else code_path.parent / "phases"
        try:
            for phase_file in sorted(resolved_phases_dir.glob("*.py")):
                hasher.update(phase_file.read_bytes())
        except OSError:
            hasher.update(b"phases-missing")
        digest = hasher.hexdigest()
        logger.debug("[IMP:6][_phase_input_hash] Phase %s input hash: %s", phase_value, digest[:12])
        return digest

    # endregion FUNC__phase_input_hash

    # region FUNC_phase_needs_rerun
    ## @purpose — True если фаза отмечена done, но её входы изменились (hash mismatch) —
    ##            content-hash инвалидация (T9.3, L-4/B-1). Участвуют deploy/converge-фазы;
    ##            прочие фазы (φ1-φ7, φ9-φ10) не зависят от modules/services → False.
    ## @io — ⇥ phase_value: str, env: Mapping | None = None (DI, W-H DevPlan 163 — override NODE_YAML
    ##          для hash-инвалидации; None = os.environ) → ⎋ bool
    ## @complexity O(N) где N = hash входов
    ## @invariants
    ##   - Только HASH_INVALIDATED_PHASES: deploy_services, deploy_update, registry_update,
    ##     converge_services, converge_update — фазы, потребляющие modules/services из node.yaml
    ##   - StepState без сохранённого hash → False (done сохраняется)
    ##   - mismatch → True (cli перевыполнит фазу, сбросив статус в pending)
    def phase_needs_rerun(self, phase_value: str, *, env: Mapping[str, str] | None = None) -> bool:
        """Return True if a done phase must re-run because its inputs changed (T9.3)."""
        if phase_value not in _HASH_INVALIDATED_PHASES:
            return False
        entry = self.state.steps.get(phase_value)
        if entry is None or not phase_is_done(entry):
            return False
        stored_hash = (
            getattr(entry, "hash", None)
            if not isinstance(entry, dict)
            else cast("str | None", cast("dict[str, object]", entry).get("hash"))  # W11-G3: raw-dict ветка → Unknown
        )
        if not stored_hash:
            return False  # hash не сохранялся → done сохраняется
        current = self._phase_input_hash(phase_value, env=env)
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
    ## @io — ⇥ required_vars: list of env var names, env: Mapping | None (DI, DevPlan 160 E2) → ⎋ bool
    ## @complexity — O(N) where N = len(required_vars)
    @staticmethod
    def validate_bootstrap_env(
        required_vars: list[str] | None = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> bool:
        """Check that all required env vars exist and are non-empty. Returns True if OK."""
        if required_vars is None:
            required_vars = ["NODE_NAME", "NODE_YAML", "PLATFORM_OWNER_KEY"]
        source: Mapping[str, str] = os.environ if env is None else env
        missing: list[str] = []
        for var in required_vars:
            val = source.get(var, "").strip()
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
    ## @purpose — Execute a single phase by calling its phase function from PHASE_DISPATCH.
    ##            Checks dependency graph first, then precondition, then executes (PhaseDispatcher,
    ##            план 170 W5-C3: 152 LOC/9 params → ~35 LOC + helper'ы).
    ## @io — ⇥ phase_value: str (from BootstrapPhase),
    ##          ctx: PhaseContext | None (НОВЫЙ контракт W5-C3: core_dir/node/env/facts/helpers),
    ##          env: Mapping | None (DI, DevPlan 160 E2), facts: EnvironmentFacts | None (E3),
    ##          phase_func_override: Callable | None (тесты ретрая/фазовых-путей),
    ##          sleep_fn: Callable | None (backoff DI, W-H), system/users/val_helpers: object | None
    ##          → ⎋ object (результат фазы: bool) ⚡ PhaseDependencyError/PhasePreconditionError
    ## @complexity — O(D + P) where D = dependency check, P = phase execution
    ## @invariants
    ##   - Dependency check before precondition check (порядок сохранён)
    ##   - Phase dependency graph: all prerequisite phases must be done
    ##   - Precondition check: intra-phase conditions verified before execution
    ##   - ОБРАТНАЯ СОВМЕСТИМОСТЬ: старые kwargs (env/facts/helpers/sleep_fn/phase_func_override)
    ##     сохранены — cli.py/тесты вызывают execute_phase без изменений; ctx= — дополнительный
    ##     канал (явные kwargs перекрывают ctx)
    ##   - env= (или self._env из __init__) — источник NODE_NAME/NODE_YAML/CORE_DIR + ключевых
    ##     env фаз (ENV_AWARE_PHASES: TOR_ENABLED/SECRETS_ENV_FILE); None = os.environ
    ##   - facts= (или self._facts из __init__) — источник is_root/path_isfile для
    ##     precondition_check + facts-aware фаз (FACTS_AWARE_PHASES); None = default_env_facts
    ##   - Ретрай-цикл извлечён в _call_with_retry (семантика 1:1: ретраится ТОЛЬКО raise-путь)
    def execute_phase(
        self,
        phase_value: str,
        *,
        ctx: PhaseContext | None = None,
        env: Mapping[str, str] | None = None,
        facts: EnvironmentFacts | None = None,
        phase_func_override: Callable[..., object] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        system_helpers: object | None = None,
        users_helpers: object | None = None,
        val_helpers: object | None = None,
    ) -> object:
        """Execute a single phase with dependency and precondition checks.

        Steps: dependency graph → precondition check → phase execution (PHASE_DISPATCH).

        Возвращает результат фазы (bool: done / done_with_warnings) — вызывающие
        (cli.run_init_mode/run_update_mode) трактуют truthy-семантику.
        """
        logger.info("[IMP:9][execute_phase] Starting phase %s", phase_value)

        pctx = self._resolve_phase_context(
            ctx,
            env=env,
            facts=facts,
            system_helpers=system_helpers,
            users_helpers=users_helpers,
            val_helpers=val_helpers,
        )

        # Step 1: Check dependency graph (drill C2: {done, done_with_warnings} удовлетворяют —
        # done_with_warnings перевыполняется САМА, но НЕ блокирует downstream)
        missing_deps = self._missing_dependencies(phase_value)
        if missing_deps:
            msg = (
                f"Phase '{phase_value}' requires prerequisite phase(s): "
                f"{', '.join(missing_deps)}. "
                f"Execute missing phases first."
            )
            raise PhaseDependencyError(msg)

        # Step 2: Check preconditions
        self.state.precondition_check(phase_value, core_dir=pctx.core_dir, env=pctx.env, facts=pctx.facts)

        # Step 3: Dispatch — статический реестр PHASE_DISPATCH (W-H: phase_func_override для тестов)
        phase_func = phase_func_override if phase_func_override is not None else PHASE_DISPATCH.get(phase_value)
        if phase_func is None:
            msg = f"Unknown phase: {phase_value}"
            raise PhaseDependencyError(msg)

        # Step 4: Execute (T9.11, B-3): _call_with_retry — транзиентные сбои ретраятся
        # RETRY_COUNT=2 с экспоненциальным backoff; False-результат (WARN) НЕ ретраится.
        kwargs = _phase_kwargs(phase_value, pctx)
        result = _call_with_retry(
            phase_func, (pctx.core_dir, pctx.node_name, pctx.node_yaml), kwargs, sleep_fn=sleep_fn
        )

        logger.info(
            "[IMP:9][execute_phase] Phase %s completed: %s",
            phase_value,
            "success" if result else "with warnings",
        )
        return result

    # endregion FUNC_execute_phase

    # region FUNC__resolve_phase_context
    ## @purpose — Сборка PhaseContext: MERGE-семантика env ({**os.environ, **env}) + резолюция
    ##            core_dir/node_name/node_yaml (ctx → self.core_dir → env → канон-дефолты).
    ##            Приоритет: явные kwargs > ctx > self._* (из __init__) > os.environ.
    ## @io — ⇥ ctx: PhaseContext | None, env/facts/helpers kwargs → ⎋ PhaseContext
    ## @complexity — O(1)
    ## @invariants
    ##   - env merge: {**os.environ, **env} — env= это override-дикт поверх os.environ
    ##   - core_dir: ctx.core_dir (явный) → self.core_dir → CORE_DIR env → platform_remote_base()/core
    ##   - helpers: kwargs > ctx > self._* (None → фаза сама берёт канонический модуль)
    def _resolve_phase_context(
        self,
        ctx: PhaseContext | None,
        *,
        env: Mapping[str, str] | None,
        facts: EnvironmentFacts | None,
        system_helpers: object | None,
        users_helpers: object | None,
        val_helpers: object | None,
    ) -> PhaseContext:
        """Resolve PhaseContext from ctx= or legacy kwargs/self-атрибутов (W4e/E3/W-H merge)."""
        base_env = self._env if ctx is None else ctx.env
        base_facts = self._facts if ctx is None else ctx.facts
        base_sys = self._system_helpers if ctx is None else ctx.system_helpers
        base_users = self._users_helpers if ctx is None else ctx.users_helpers
        base_val = self._val_helpers if ctx is None else ctx.val_helpers
        phase_env = env if env is not None else base_env
        phase_facts = facts if facts is not None else base_facts
        # W4e (DevPlan 160 E2): MERGE-семантика — env= override-дикт поверх os.environ
        source: Mapping[str, str] = os.environ if phase_env is None else {**os.environ, **phase_env}
        ctx_core = ctx.core_dir if ctx is not None else None
        core_dir = ctx_core or self.core_dir or source.get("CORE_DIR", str(platform_remote_base() / "core"))
        node_name = (ctx.node_name if ctx is not None else "") or source.get("NODE_NAME", "")
        node_yaml = (ctx.node_yaml if ctx is not None else "") or source.get("NODE_YAML", "")
        return PhaseContext(
            core_dir=core_dir,
            node_name=node_name,
            node_yaml=node_yaml,
            env=source,
            facts=phase_facts,
            system_helpers=system_helpers if system_helpers is not None else base_sys,
            users_helpers=users_helpers if users_helpers is not None else base_users,
            val_helpers=val_helpers if val_helpers is not None else base_val,
        )

    # endregion FUNC__resolve_phase_context

    # region FUNC__missing_dependencies
    ## @purpose — Проверка dependency graph: список неудовлетворённых prerequisite-фаз.
    ## @io — ⇥ phase_value: str → ⎋ list[str] (пустой = все deps удовлетворены)
    ## @complexity — O(D) где D = число зависимостей фазы
    ## @invariants
    ##   - Статус-набор удовлетворения зависимости = {done, done_with_warnings} (drill C2):
    ##     WARN-фаза перевыполняется САМА (phase_is_done strict — re-run/exit-code), НО
    ##     удовлетворяет downstream — иначе один warning навсегда рвёт цепочку update
    ##   - failed/pending/skipped/running НЕ удовлетворяют (guard против незавершённых)
    def _missing_dependencies(self, phase_value: str) -> list[str]:
        """Return prerequisite phases that are not satisfied (dependency gate)."""
        deps = _phase_dependency_graph.get(phase_value, set())
        missing: list[str] = []
        for dep in deps:
            phase_state = self.state.steps.get(dep, self._state_from_phase_key(dep))
            # 🧐 TRAP[DECISION] · 2026-08-27 · — · Dependency-gate {done, done_with_warnings} (drill C2): WARN-фаза удовлетворяет downstream, перевыполняется САМА · Rejected: расширять phase_is_done (сломало бы WARN-перевыполнение + strict-init exit-code) · Reason: отдельный предикат — единственная точка гейта · Rev: если WARN-фаза должна блокировать downstream — вернуть phase_is_done сюда
            if not phase_satisfies_dependency(phase_state):
                missing.append(dep)
        return missing

    # endregion FUNC__missing_dependencies

    def _state_from_phase_key(self, phase_key: str) -> dict[str, object]:
        """Get phase state from state dict using the phase key directly.

        ## @purpose — Helper to look up phase state in both old (steps dict) and new format.
        ## @io — ⇥ phase_key: str → ⎋ dict (state entry or empty)
        ## @complexity — O(1)
        """
        step_entry = self.state.steps.get(phase_key)
        if step_entry is not None:
            if hasattr(step_entry, "to_dict"):
                return cast(
                    "dict[str, object]", step_entry.to_dict()
                )  # W11-G3: dict invariance (значения str|list[str] → object)
            if isinstance(step_entry, dict):
                return step_entry
            return {"status": step_entry.status if hasattr(step_entry, "status") else "pending"}

        # Also check top-level state for phase keys
        # W11-G3: state-dict граница (getattr/to_dict → Unknown) — каст к Mapping[str, object]
        state_dict = cast("Mapping[str, object] | None", getattr(self.state, "state_dict", None))
        if state_dict is None:
            try:
                state_dict = cast("Mapping[str, object]", self.state.to_dict())
            except (AttributeError, TypeError):  # noqa: EXC — best-effort
                return {}
        raw_steps = cast("Mapping[str, object]", state_dict.get("steps", {}))
        return cast("dict[str, object]", raw_steps.get(phase_key, {}))

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
                    status = (
                        "done" if cast("dict[str, object]", phase_state).get("done") else "pending"
                    )  # W11-G3: raw-dict ветка
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


# Единственный канал запуска — node-lifecycle.sh → lifecycle/cli.py (python3 -m).
# Compat-заглушка прямого запуска удалена (164 W3): прямые вызовы state_machine.py
# не поддерживаются — честный отказ вместо скрытого дублирующего канала.
