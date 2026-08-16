"""core.internal.static — единый AST/структурный проход статического слоя (DevPlan 163 W-C).

# GREP_SUMMARY: static package ast structural detectors cli agent-check fast-layer DevPlan-163 version
# STRUCTURE: ┌Finding┐ → ┌registry (DETECTORS/run_all)┐ → ┌CLI (__main__.py check [--changed] [--json])┐ → ⎋
"""
# region MODULE_CONTRACT
## @purpose  Пакет статического слоя ai-platform (DevPlan 163 W-C, T2.1): ~20 grep-гейтов
##           pytest-слоя превращаются в детерминированные AST/структурные детекторы
##           быстрого слоя агента (L1: <5 s после правки). CLI:
##           `python3 -m core.internal.static check [--changed] [--json]`.
## @scope    core/internal/static/** (этот пакет), tests/unit/test_static_*.py (R5-пары),
##           миграция grep-гейтов — фаза 2 (DevPlan 163 §4.3 прямое замещение).
## @invariants
##   - Пакет — единственный владелец детекторов (никаких копий в тестах)
##   - Каждый детектор: detect(root: Path, changed: set[str] | None) -> list[Finding]
##   - Находки — Finding(rule, file, line, message, severity); severity "error" = blocking
##   - Чистота: файлы пакета проходят текущий ruff.toml И планируемый строгий конфиг
##     (EM/BLE/FBT/S/PTH/PLR/ARG/SLF/PLW/TRY/PLC0415-каноны), basedpyright 0 errors
## @rationale Инверсия слоёв (01-Brief §2): статика жила в медленном pytest-слое (~3-5 мин)
##            и отсутствовала в быстром. Пакет — целевой сдвиг: классы дефектов гейтов
##            детектируются <5 s, тесты остаются для поведенческих гарантий.
## @changes 2026-08-13 | DevPlan 163 W-C C1 — Created
# endregion MODULE_CONTRACT

from __future__ import annotations

from core.internal.static.finding import Finding
from core.internal.static.registry import DETECTORS, DetectorSpec, count_by_rule, human_report, json_report, run_all

__version__ = "0.1.0"

__all__ = [
    "DETECTORS",
    "DetectorSpec",
    "Finding",
    "__version__",
    "count_by_rule",
    "human_report",
    "json_report",
    "run_all",
]
