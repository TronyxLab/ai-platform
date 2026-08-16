#!/usr/bin/env python3
# GREP_SUMMARY: state-store, step-state, bootstrap-state, state-json, load-state, save-state, persistence, checkpoint
# STRUCTURE: ▶ StepState (name/status/hash/timing/warnings) → BootstrapState (mode/node/steps/errors/warnings + precondition_check-обёртка) → ⚡ load_state(path) ┌json.load┐ → ⚡ save_state(state, path) ┌atomic tmp+replace┐ → ⎋
# region MODULE_CONTRACT
## @purpose  State persistence для bootstrap lifecycle — StepState/BootstrapState dataclasses +
##           state.json I/O (load_state/save_state), извлечено из state_machine (B9 T2, U-08).
## @scope    state_store.py: StepState, BootstrapState (включая ТОНКУЮ ОБЁРТКУ precondition_check),
##           load_state(path), save_state(state, path).
##           Логика прекондишенов переехала в phases/preconditions.py (план 170 W5-C1);
##           _check_command_exists удалён (→ preconditions, shutil.which).
##           state_machine.py импортирует и re-экспортирует BootstrapState/StepState —
##           публичный контракт пакета (тесты и cli.py не меняют импорты).
## @invariants
##   - BootstrapState.from_dict читает name-based step-ключи
##   - precondition_check — BLOCKING intra-phase валидация; ошибки человекочитаемы;
##     обёртка делегирует phases.preconditions.check_phase (registry PRECONDITIONS)
##   - save_state — атомарная запись (unique tmp через shared atomic_writer + fsync + replace)
##     ПОД flock на state.json.lock (сериализация конкурентных writers, DevPlan 136 W9 T9.2)
##   - load_state — коррапт state.json → StateCorruptError (ЯВНАЯ ошибка, НЕ свежий state —
##     DevPlan 136 W9 T9.2; свежий state маскировал бы потерю checkpoint'ов)
##   - НЕТ импорта state_machine (направление зависимостей: state_machine → state_store);
##     PhasePreconditionError/PhaseDependencyError — из leaf exceptions.py (W10-design п.1)
##   - НОВОЕ ребро state_store → phases (preconditions): одностороннее (phases НЕ импортирует
##     state_store) — цикл №2 (state_store ↔ state_machine) разорван
## @rationale DevPlan 116 B9 D2: persistence (~270 LOC) вынесена в state_store.py —
##            state_machine.py остаётся чистой оркестрацией (~950 LOC, запас под гейт ≤1200).
##            DevPlan 136 W9 T9.2 (L-2/B-2): save_state писал в ФИКСИРОВАННЫЙ tmp
##            (path.with_suffix('.json.tmp')) — два конкурентных writers (retry double-deploy,
##            bootstrap + node-update параллельно) могли перезаписать tmp друг друга и
##            os.replace'ить чужие данные; коррапт молча сбрасывался в свежий state.
##            W5-C1 (план 170): precondition_check (139 LOC/CC30/depth17) жил в persistence-модуле
##            → phases/preconditions.py; state_store остаётся persistence-only (обёртка-фасад).
## @changes  2026-08-01 · Extracted from state_machine (B9 T2)
## @changes  2026-08-05 · DevPlan 136 W9 T9.2 — flock + unique tmp save; StateCorruptError load
## @changes  2026-08-06 · DevPlan 140 W4 — precondition φ4: env-цепочка первична
##            (AGE_SECRET_KEY/SOPS_AGE_KEY/AGE_SECRET_KEY_FILE), /etc/age/key.txt — restore-first fallback
## @changes  2026-08-13 · DevPlan 160 E3 — precondition_check +facts: EnvironmentFacts
##            (is_root/path_isfile DI вместо os.geteuid/os.path.isfile)
## @changes  2026-08-14 · план 170 W2-A1 — мёртвый код удалён: пустые precondition-ветки
##            (secrets_update / node_config_update / registry_update — pass-блоки) и
##            пустой блок `if TYPE_CHECKING: pass`
## @changes  2026-08-15 · план 170 W5-C1 — precondition_check → тонкая обёртка над
##            phases/preconditions.check_phase; _check_command_exists удалён (→ shutil.which);
##            lazy-импорт PhasePreconditionError убран (exceptions.py leaf); чистка импортов
## ⚠️ TRAP[DECISION] · — · Legacy-ветки совместимости from_dict («без or»-чтение + толерантность
##   к отсутствующим ключам старого формата state.json) сохранены (177 W4 S10): удаление
##   требует верификации формата на живых нодах. · Rev: после релиза 1.0.0 (ноды
##   перебутстраплены → state.json только свежего формата) — проверить формат на обеих
##   нодах и удалить ветки совместимости.
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

# W5-C1: прекондишены переехали в phases/preconditions.py (registry PRECONDITIONS).
# НОВОЕ ребро state_store → phases — одностороннее (phases НЕ импортирует state_store).
from core.internal.bootstrap.lifecycle.phases.preconditions import check_phase as _check_phase

# Единый атомарный writer (unique tmp + fsync + os.replace) —
# фиксированный tmp (with_suffix('.json.tmp')) был гонкой для конкурентных writers.
from core.internal.shared.atomic_writer import atomic_write_json as _atomic_write_json

# E3 (160): facts DI тип для precondition_check-обёртки (is_root/path_isfile)
from core.internal.shared.env_facts import EnvironmentFacts

# flock на state.json.lock (reentrant, blocking с таймаутом).
from core.internal.shared.file_lock import FileLock as _FileLock

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

    def to_dict(self) -> dict[str, str | list[str]]:
        """Serialize to dict for JSON state file."""
        d: dict[str, str | list[str]] = {"name": self.name, "status": self.status}
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
    def from_dict(cls, data: Mapping[str, object]) -> StepState:
        """Deserialize from dict.

        ## @purpose — Десериализация StepState из state.json-записи.
        ## @invariants
        ##   - status приоритетен; при отсутствии — 'pending'
        ##   - name может отсутствовать (заполняется ключом на load_state)
        ## @changes 2026-08-15 | W11-G3 — JSON-граница типизирована Mapping[str, object]
        ##            (json.load → Any); cast-ы фиксируют контракт полей (runtime 1:1)
        """
        # W11-G3: JSON-граница — поля state.json по контракту строки/списки; cast без `or`
        # (runtime-семантика 1:1, включая отсутствующие ключи/None-значения legacy-файлов).
        return cls(
            name=cast(str, data.get("name", "")),
            status=cast(str, data.get("status", "pending")),
            hash=cast(str | None, data.get("hash")),
            started_at=cast(str | None, data.get("started_at")),
            error=cast(str | None, data.get("error")),
            reason=cast(str | None, data.get("reason")),
            warnings=cast(list[str], data.get("warnings", [])),
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

    def to_dict(self) -> dict[str, object]:
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
    def from_dict(cls, data: Mapping[str, object]) -> BootstrapState:
        """Deserialize from dict (name-based step keys).

        ## @changes 2026-08-15 | W11-G3 — JSON-граница типизирована Mapping[str, object]
        ##            (json.load → Any); cast-ы фиксируют контракт полей (runtime 1:1)
        """
        # W11-G3: JSON-граница — cast без `or` (runtime-семантика 1:1, legacy-файлы)
        raw_steps = cast("Mapping[str, object]", data.get("steps", {}))
        steps = {k: StepState.from_dict(cast("Mapping[str, object]", v)) for k, v in raw_steps.items()}
        return cls(
            mode=cast(str, data.get("mode", "init")),
            node=cast(str | None, data.get("node")),
            current_step=cast(int, data.get("current_step", 0)),
            steps=steps,
            errors=cast(list[str], data.get("errors", [])),
            warnings=cast(list[str], data.get("warnings", [])),
        )

    # region FUNC_precondition_check
    ## @purpose — Validate intra-phase conditions before execution (ТОНКАЯ ОБЁРТКА,
    ##            W5-C1: логика переехала в phases/preconditions.py — registry PRECONDITIONS).
    ##            Контракт state_machine.execute_phase и тестов сохранён 1:1.
    ## @io — ⇥ phase_value: str (from BootstrapPhase), core_dir: str | None,
    ##          env: Mapping | None (DI — NODE_YAML/AGE_*/GHCR_PULL_TOKEN/CORE_DIR, DevPlan 160 E2),
    ##          facts: EnvironmentFacts | None (DI — is_root/path_isfile, DevPlan 160 E3)
    ##       → ⎋ None (raises PhasePreconditionError on failure)
    ## @complexity — O(1) (delegation)
    ## @invariants
    ##   - Delegate: preconditions.check_phase(phase_value, core_dir=..., env=..., facts=...)
    ##   - precondition failures are BLOCKING — phase will not execute
    ##   - Error message is human-readable for operator action
    ##   - facts= None → default_env_facts() (поведение неизменно); root-сообщение сохраняет
    ##     euid-детализацию ТОЛЬКО когда facts не предоставлен (real env)
    @staticmethod
    def precondition_check(
        phase_value: str,
        core_dir: str | None = None,
        *,
        env: Mapping[str, str] | None = None,
        facts: EnvironmentFacts | None = None,
    ) -> None:
        """Validate preconditions for a given phase value. Raises PhasePreconditionError on failure.

        ## @io — ⇥ phase_value: BootstrapPhase, core_dir: str | None (StateMachine.core_dir
        ##        — единый источник резолюции; fallback: CORE_DIR env → /opt/platform/core),
        ##        env: Mapping | None (env-дикт от execute_phase; None = os.environ),
        ##        facts: EnvironmentFacts | None (DI; None = default_env_facts)
        ## @invariants
        ##   - core_dir передаётся из StateMachine (self.core_dir, установлен CLI из PLATFORM_ROOT);
        ##     BootstrapState не хранит core_dir — параметр, не атрибут.
        ##   - PhasePreconditionError raise'ится из phases/preconditions (exceptions.py leaf) —
        ##     state_store НЕ импортирует state_machine (направление state_machine → state_store).
        ##   - env= — override для NODE_YAML (φ5) и прочих env-прекондишенов (W4e, DevPlan 160 E2)
        ##   - facts= — override is_root/path_isfile (E3, DevPlan 160): тесты прекондишенов
        ##     без monkeypatch os.geteuid/os.path.isfile
        ##   - staticmethod (W5-C1): метод не использует self (тонкая обёртка) — PLR6301
        """
        _check_phase(phase_value, core_dir=core_dir, env=env, facts=facts)

    # endregion FUNC_precondition_check


# endregion FUNC_BootstrapState


# region CLASS_StateCorruptError
class StateCorruptError(Exception):
    """Raised by load_state when state.json is corrupt (invalid JSON / wrong structure).

    ## @purpose — DevPlan 136 W9 T9.2 (L-2/B-2): коррапт state.json НЕ маскируется свежим
    ##            state'ом (потеря checkpoint'ов: фазы, помеченные done, молча сбрасываются →
    ##            повторный полный bootstrap; или наоборот — pending-фазы становятся fresh).
    ##            Явная ошибка → оператор восстанавливает файл или запускает с --force.
    ## @io — ⇥ message → ⎋ StateCorruptError instance
    ## @complexity O(1)
    """


# endregion CLASS_StateCorruptError


# region FUNC_load_state
## @purpose  Load BootstrapState from state.json path. Raises StateCorruptError on corrupt.
## @io       ⇥ path: Path → ⎋ BootstrapState ⚡ StateCorruptError
## @complexity O(N) where N = number of steps in existing state
## @invariants
##   - Отсутствующий файл → свежий state (не коррапт — нормальный первый запуск)
##   - Коррапт state.json (JSONDecodeError/KeyError/ValueError) → StateCorruptError (ЯВНАЯ ошибка)
def load_state(path: Path) -> BootstrapState:
    """Load bootstrap state from JSON file.

    Raises StateCorruptError on corrupt state (T9.2 — explicit, NOT fresh state).
    """
    if not path.exists():
        logger.info("[IMP:7][StateMachine][init] No state file at %s — creating fresh", path)
        return BootstrapState()

    logger.info("[IMP:7][StateMachine][init] Loading state from %s", path)
    try:
        with Path(path).open(encoding="utf-8") as f:
            data = cast("Mapping[str, object]", json.load(f))  # W11-G3: json.load → Any; JSON-граница state.json
        # Load directly from steps — state.json содержит только name-based step-ключи
        # (system_bootstrap, user_accounts, …).
        state = BootstrapState.from_dict(data)
        logger.info(
            "[IMP:8][StateMachine][init] State loaded: mode=%s node=%s current_step=%d",
            state.mode,
            state.node,
            state.current_step,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        # ⚠️ TRAP[BUG] · 2026-08-05 · HI · коррапт state.json молча сбрасывался в свежий state
        # · Symptom: node-update на ноде с повреждённым state.json тихо начинал всё заново
        #   (L-2/B-2, DevPlan 136 W9 T9.2) — checkpoint'ы терялись без следа.
        # · Root: except-ветка возвращала BootstrapState() (fresh) — «не свежий state» требование.
        # · Fix: StateCorruptError с путём и recovery-инструкцией; StateMachine.__init__ оборачивает
        #   в PlatformFatalError; --force (cli.main) удаляет файл и стартует заново.
        # · Prevention: коррапт = фатальная ошибка, не тихий сброс; единственный reset — --force.
        logger.error(
            "[IMP:10][StateMachine][init] Corrupt state file %s: %s — remove it or run with --force "
            "(T9.2: explicit error, NOT fresh state)",
            path,
            e,
        )
        msg = f"State file {path} is corrupt: {e}. Remove the file or re-run with --force."
        raise StateCorruptError(msg) from e
    else:
        return state


# endregion FUNC_load_state


# region FUNC_save_state
## @purpose  Persist state to JSON file atomically (unique tmp + flock) — T9.2.
##           Creates parent dirs. Writers сериализуются через FileLock на state.json.lock.
## @io       ⇥ state: BootstrapState, path: Path → ⎋ None ⚡ OSError (re-raise), FileLockError
## @complexity O(N) where N = number of steps
## @invariants
##   - Атомарная запись: unique tmp (shared atomic_writer: mkstemp + fsync) → replace —
##     коррапт state.json невозможен при crash; конкурентные writers не делят один tmp
##   - flock на {path}.lock (reentrant, blocking, timeout 30s): два процесса, пишущих
##     state.json одновременно, сериализуются (last-writer-wins, без tearing)
##   - OSError → RE-RAISE (fatal) — потеря state недопустима
##   - FileLockError (контеншн > timeout) → RE-RAISE — тихий сброс чужого writers опасен
def save_state(state: BootstrapState, path: Path) -> None:
    """Write state to JSON file atomically (unique tmp + replace) under flock (T9.2)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")  # state.json → state.json.lock
    lock = _FileLock(lock_path, timeout=30.0)
    lock.acquire()
    try:
        # Shared atomic_writer: NamedTemporaryFile в той же директории →
        # flush + fsync → os.replace. Фиксированный tmp (.json.tmp) не используется — гонка writers.
        _atomic_write_json(path, state.to_dict(), mode=0o644)
        # IMP:9 business logic: state persisted atomically (flock + unique tmp → fsync → replace).
        # D-136-state-store-IMP9 (DevPlan 145 W3): подтверждающий лог успешной персистенции
        # с путём, режимом и числом фаз — коррапт/гонка детектируется по отсутствию этой строки.
        step_count = len(state.steps)
        logger.info(
            "[IMP:9][StateMachine][save] State persisted: path=%s mode=%s node=%s mode_run=%s steps=%d",
            path,
            "0o644",
            state.node,
            state.mode,
            step_count,
        )
    except OSError as e:
        logger.error("[IMP:10][StateMachine][save] Failed to save state: %s", e)
        raise
    finally:
        lock.release()


# endregion FUNC_save_state
