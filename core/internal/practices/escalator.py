#!/usr/bin/env python3
# GREP_SUMMARY: practices-escalator, state-machine, baseline, proposed, active-full, no-autopromote, evaluate, PracticesState, EscalatorDecision
# STRUCTURE: ▶ evaluate(maturity, level_setting, lock) → ◇ level=baseline → forced BASELINE → ◇ level=full → forced ACTIVE_FULL → ◇ level=auto → maturity is_propose → PROPOSED|BASELINE → ⎋ EscalatorDecision(state, reason, warning)
# region MODULE_CONTRACT
## @purpose  Эскалатор зрелости практик (DevPlan 137 §4.6): 3 состояния (baseline|proposed|
#            active-full), переходы по таблице §4.6, БЕЗ автопромоута (решение пользователя
##           2026-08-05: «варнинга хватит» — active-full включается ТОЛЬКО по явному
##           set-practices full). Вычисляется ТОЛЬКО там, где есть git (локально K1/K5, CI K2);
##           на VPS verify применяет готовый state из practices.lock (evaluate() НЕ вызывается).
## @scope    Потребители: check_project.py, sync_practices.py, set_practices.py,
##           gen_project_platform_md.py (Practices-секция), project_scaffolder.py (шаг 11).
##           W1-скоуп: полная state-машина §4.6 (нужна для initial state=baseline в scaffold,
##           [PRACTICES:PROPOSE] в check_project и maturity-снапшота lock); W3 расширяет
##           аудит-записи переходов (audit_logger) и pre-push интеграцию K5.
## @invariants
##   - 3 состояния, явно (аудит/тестируемость): baseline → proposed → active-full
##   - level=baseline|full → ФОРС состояния (независимо от maturity); level=auto → maturity решает
##   - Автопромоута НЕТ: НИКАКОЙ переход в active-full из auto (только forced full)
##   - proposed: стабилен до ручного действия (нет авто-перехода обратно в baseline)
##   - active-full: терминален для auto (только ручной откат set-practices baseline)
##   - warning: [PRACTICES:PROPOSE] только в proposed; None в baseline/active-full (инфо-стиль)
## @rationale E5-гибрид (DevPlan 137 §4.4): плавно + явное согласие. Упрощение 3 состояний
##            без счётчиков на ноде (shadow-full и proposed объединены — автопромоут отклонён).
## @changes  2026-08-05 · DevPlan 137 W1 — создан (state-машина §4.6)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from core.internal.practices.manifest import maturity_thresholds
from core.internal.practices.maturity import Maturity

logger = logging.getLogger(__name__)


# region FUNC_PracticesState
## @purpose  Enum 3 состояний эскалатора практик (DevPlan 137 §2.1A).
## @io       ⇥ value: str → ⎋ PracticesState (baseline|proposed|active-full)
## @complexity O(1)
class PracticesState(Enum):
    """Practices escalator states (3 explicit states — auditability)."""

    BASELINE = "baseline"  # только baseline-набор + L1
    PROPOSED = "proposed"  # full-набор non-blocking + [PRACTICES:PROPOSE] (бывшие shadow+proposed)
    ACTIVE_FULL = "active-full"  # full-набор, L1+L2+L3 блокируют (ТОЛЬКО по согласию)


# endregion FUNC_PracticesState


# region FUNC_EscalatorDecision
## @purpose  Frozen-результат evaluate(): состояние + причина + варнинг-предложение агенту.
## @io       ⇥ state/reason/warning → ⎋ EscalatorDecision
## @complexity O(1)
@dataclass(frozen=True)
class EscalatorDecision:
    """Escalator decision: state + reason + optional [PRACTICES:PROPOSE] warning."""

    state: PracticesState
    reason: str  # "age=41d,files=87" | "manual: baseline" | "manual: full" | "fresh"
    warning: str | None  # "[PRACTICES:PROPOSE]..." или None

    ## @purpose  Краткое имя состояния для lock/AI-PLATFORM.md.
    ## @io       ⎋ str — "baseline"|"proposed"|"active-full"
    ## @complexity O(1)
    @property
    def state_name(self) -> str:
        """Render state value (lock-safe string)."""
        return self.state.value


# endregion FUNC_EscalatorDecision


# ── Уровни из ai-platform.yaml#quality.level ──
LEVEL_BASELINE = "baseline"
LEVEL_FULL = "full"
LEVEL_AUTO = "auto"
_VALID_LEVELS: frozenset[str] = frozenset({LEVEL_BASELINE, LEVEL_FULL, LEVEL_AUTO})

# ── Варнинг-шаблон (единый формат, DevPlan 137 §4.3) ──
_WARNING_TPL = (
    "[PRACTICES:PROPOSE][level:full][reason:{reason}]"
    "\n>>> RECOMMEND: make project-set-practices full (или make project-sync-practices для обновления канона)"
)


# region FUNC_validate_level_setting
## @purpose  Валидация level_setting (baseline|full|auto) — fail-fast на невалидном значении.
##           Типизированная ошибка ConfigValidationError (exit 4 — контракт B4, гейт
##           no_bare_raise: bare ValueError/RuntimeError запрещены в core/).
## @io       ⇥ level: str → ⎋ str (каноническое значение)
##           ⚡ ConfigValidationError — вне {baseline, full, auto} (exit 4)
## @complexity O(1)
def validate_level_setting(level: str) -> str:
    """Validate level setting (baseline|full|auto); ConfigValidationError on invalid."""
    if level not in _VALID_LEVELS:
        from core.internal.shared.exceptions import ConfigValidationError

        raise ConfigValidationError(f"Invalid practices level: '{level}' (expected baseline|full|auto)")
    return level


# endregion FUNC_validate_level_setting


# region FUNC_evaluate
## @purpose  State-машина эскалатора (таблица переходов §4.6). Вход: maturity (git-доступен),
##           level_setting (ai-platform.yaml quality.level), lock (PracticesLock снапшот —
##           для proposed-стабильности/terminal active-full при auto).
## @io       ⇥ maturity: Maturity, level_setting: str, lock: PracticesLock | None → ⎋ EscalatorDecision
## @complexity O(1)
## @invariants
##   - level=baseline → FORCED baseline (откат-форс, независимо от maturity) — warning=None
##   - level=full → FORCED active-full (согласие пользователя) — warning=None
##   - level=auto + maturity.is_propose → proposed + [PRACTICES:PROPOSE] warning
##   - level=auto + не propose → baseline (свежий проект)
##   - level=auto + lock.state ∈ {proposed, active-full} → СТАБИЛЬНО (предыдущее состояние
##     сохраняется — proposed стабилен до ручного действия, active-full терминален)
##   - НЕТ автоперехода в active-full (решение пользователя 2026-08-05)
def evaluate(
    maturity: Maturity,
    level_setting: str,
    lock: object | None = None,
) -> EscalatorDecision:
    """Evaluate escalator state per §4.6 transition table (no auto-promote)."""
    validate_level_setting(level_setting)
    thresholds = maturity_thresholds()

    if level_setting == LEVEL_BASELINE:
        logger.info("[IMP:9][escalator][evaluate] level=baseline → FORCED baseline (manual)")
        return EscalatorDecision(state=PracticesState.BASELINE, reason="manual: baseline", warning=None)

    if level_setting == LEVEL_FULL:
        logger.info("[IMP:9][escalator][evaluate] level=full → FORCED active-full (user consent)")
        return EscalatorDecision(state=PracticesState.ACTIVE_FULL, reason="manual: full", warning=None)

    # ── level=auto: maturity решает; lock стабилизирует предыдущее состояние ──
    prev_state: PracticesState | None = None
    if lock is not None:
        lock_state = getattr(lock, "state", None)
        prev_state = _coerce_state(lock_state)

    if prev_state in (PracticesState.PROPOSED, PracticesState.ACTIVE_FULL):
        # Стабильно до ручного действия (proposed) / терминально (active-full) — без авто-перехода
        reason = f"stable:{prev_state.value}"
        warning = _build_propose_warning(maturity, thresholds) if prev_state is PracticesState.PROPOSED else None
        logger.info(
            "[IMP:9][escalator][evaluate] level=auto, lock.state=%s → stable (no auto-promote)", prev_state.value
        )
        return EscalatorDecision(state=prev_state, reason=reason, warning=warning)

    if maturity.is_propose(thresholds):
        reason = maturity.reason(thresholds)
        logger.info(
            "[IMP:9][escalator][evaluate] level=auto, maturity exceeds thresholds (%s) → PROPOSED",
            reason,
        )
        return EscalatorDecision(
            state=PracticesState.PROPOSED,
            reason=reason,
            warning=_build_propose_warning(maturity, thresholds),
        )

    logger.info("[IMP:9][escalator][evaluate] level=auto, fresh project → BASELINE")
    return EscalatorDecision(state=PracticesState.BASELINE, reason="fresh", warning=None)


# endregion FUNC_evaluate


# region FUNC__build_propose_warning
## @purpose  Варнинг [PRACTICES:PROPOSE] в едином формате (§4.3) с reason maturity.
## @io       ⇥ maturity, thresholds → ⎋ str
## @complexity O(1)
def _build_propose_warning(maturity: Maturity, thresholds: dict[str, int]) -> str:
    """Build [PRACTICES:PROPOSE] warning line with maturity reason."""
    return _WARNING_TPL.format(reason=maturity.reason(thresholds))


# endregion FUNC__build_propose_warning


# region FUNC__coerce_state
## @purpose  Безопасная конверсия строкового state из lock → PracticesState | None.
## @io       ⇥ value: object | None → ⎋ PracticesState | None
## @complexity O(1)
def _coerce_state(value: object | None) -> PracticesState | None:
    """Coerce lock state string → PracticesState (None on unknown/missing)."""
    if value is None:
        return None
    try:
        return PracticesState(str(value))
    except ValueError:
        logger.warning("[IMP:6][escalator][coerce] Unknown lock state: %s — treating as None", value)
        return None


# endregion FUNC__coerce_state
