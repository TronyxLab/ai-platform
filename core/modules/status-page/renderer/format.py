# GREP_SUMMARY: status-page renderer format format-bytes compute-uptime uptime-human auto-unit
# STRUCTURE: ▶ format_bytes (auto-unit B/KB/MB/GB/TB) → ▶ compute_uptime_human (ISO → "3h 15m"|"< 1m"|"—")
# region MODULE_CONTRACT
## @purpose  Formatting helpers for status-page renderer (extracted from renderer.py,
##           DevPlan 170 W7-E2). Pure functions — no I/O, no state.
## @scope    Consumed by renderer/enrich.py and renderer/context.py
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - format_bytes auto-unit B/KB/MB/GB/TB, "0 B" for zero/None/negative
##   - compute_uptime_human returns "—" for None/unparseable, "< 1m" under 60s
## @rationale  DevPlan 170 W7-E2 — format.py extracted verbatim from renderer.py (AC-G7).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from renderer.py
# endregion MODULE_CONTRACT

from datetime import datetime, timezone

_KB: int = 1024  # байт в килобайте (формат размеров)
_UPTIME_MIN_S: int = 60  # < 1 минуты → "< 1m"


# region FUNC_compute_uptime_human
def compute_uptime_human(started_at: str | None) -> str:
    """Convert ISO 8601 started_at timestamp to human-readable uptime string.

    # ▶ ┌started_at ISO string┐ → ◇ None? → ⎋ "—"
    #                           → ◇ parse → ⊕ timedelta → format → ⎋ "3h 15m" or "< 1m"

    Returns human-readable duration like "3h 15m", "45m", "< 1m".
    Returns "—" if started_at is None or unparseable.
    """
    if not started_at:
        return "\u2014"

    # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
    try:
        # Handle Z suffix
        started_at_clean = started_at[:-1] + "+00:00" if started_at.endswith("Z") else started_at

        started = datetime.fromisoformat(started_at_clean)
        now = datetime.now(timezone.utc)
        delta = now - started
        total_seconds = delta.total_seconds()

        if total_seconds < 0:
            return "\u2014"
        if total_seconds < _UPTIME_MIN_S:
            return "< 1m"

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
    except (ValueError, TypeError):
        return "\u2014"
    else:
        return f"{minutes}m"


# endregion FUNC_compute_uptime_human


# region FUNC_format_bytes
## @purpose  Format bytes to human-readable string with auto unit selection (B/KB/MB/GB/TB)
## @io       ⇥ bytes_val: int, precision: int = 1 → ⎋ str
## @complexity  O(1) — <5 comparisons
def format_bytes(bytes_val: int, precision: int = 1) -> str:
    """Format bytes to human-readable string with auto unit selection.

    # ▶ ┌bytes_val┐ → ◇ < 1024 → "N B"
    #                  → ◇ < 1024² → "N.M KB"
    #                  → ◇ < 1024³ → "N.M MB"
    #                  → ◇ < 1024⁴ → "N.M GB"
    #                  → ⎋ "N.M TB"

    Returns "0 B" for zero/None/negative values.
    """
    if not bytes_val or bytes_val <= 0:
        return "0 B"
    if bytes_val < _KB:
        return f"{bytes_val} B"
    if bytes_val < _KB**2:
        return f"{bytes_val / 1024:.{precision}f} KB"
    if bytes_val < 1024**3:
        return f"{bytes_val / (1024**2):.{precision}f} MB"
    if bytes_val < 1024**4:
        return f"{bytes_val / (1024**3):.{precision}f} GB"
    return f"{bytes_val / (1024**4):.{precision}f} TB"


# endregion FUNC_format_bytes
