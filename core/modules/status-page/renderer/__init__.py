# GREP_SUMMARY: status-page renderer facade re-export format enrich context render-html format-bytes uptime
# STRUCTURE: ┌renderer/ package facade┐ → re-export format + enrich + context → ⎋ app.py API (back-compat)
# region MODULE_CONTRACT
## @purpose  Facade of status-page renderer package (DevPlan 170 W7-E2). Re-exports all names
##           consumed by app.py and existing tests — backward-compatible paths
##           (renderer.render_html, renderer.format_bytes, renderer.compute_uptime_human,
##           renderer.enrich_projects, renderer.enrich_containers).
## @scope    Decomposition of renderer.py (320 LOC) into {format, enrich, context}.py
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - All names below are re-exports — behavior defined in the submodules (single implementation)
## @rationale  DevPlan 170 W7-E2 — фасад = единая точка импорта; renderer.render_html сохранён
##            (research-A §6, 4 тест-файла).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — created (renderer.py → package)
# ⚠️ TRAP[DECISION] · 2026-08-15 · — · Приватный алиас _enrich_services_with_checks в фасаде —
# ·   публичное имя enrich_services_with_checks + приватный алиас (канон static private-imports)
# · Rejected: экспорт только публичного имени (тесты/app не используют приватное напрямую)
# · Reason: симметрия с collectors-фасадом; renderer/__init__ единый re-export-контракт пакета
# · Rev: если тесты мигрируют на renderer.enrich_services_with_checks → снять алиас.
# endregion MODULE_CONTRACT

from .context import render_html
from .enrich import enrich_containers, enrich_projects
from .enrich import enrich_services_with_checks as _enrich_services_with_checks
from .format import compute_uptime_human, format_bytes

__all__ = [
    "_enrich_services_with_checks",
    "compute_uptime_human",
    "enrich_containers",
    "enrich_projects",
    "format_bytes",
    "render_html",
]
