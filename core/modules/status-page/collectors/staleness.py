# GREP_SUMMARY: status-page collectors staleness compute-staleness stale-metrics freshness threshold
# STRUCTURE: ▶ ┌generated_at ISO┐ → ◇ None? → ⎋ None → ◇ Z→+00:00 → ◇ age > 5min? → ⎋ "Xm Ys" → ⎋ None
# region MODULE_CONTRACT
## @purpose  Staleness computation for status-page metrics — extracted from collectors.py
##           (DevPlan 170 W7-E2). Pure function; 5-minute freshness threshold.
## @scope    Consumed by collectors/aggregate.py and collectors/readiness.py (both probe paths)
## @invariants
##   - _STALE_SECONDS = 300 (5 min) — единый порог для /health и /healthz (M-7 sync)
##   - compute_staleness: >5 min → "Xm Ys" description, else None
##   - Invalid generated_at → fallback None + IMP:7 warning with repr of input (170 W2-A2, B3)
## @rationale  DevPlan 170 W7-E2 — staleness extracted verbatim from collectors.py (no behavior change).
## @changes  2026-08-15 · DevPlan 170 W7-E2 — extracted from collectors.py
# endregion MODULE_CONTRACT

import sys
from datetime import datetime, timezone

_STALE_SECONDS: int = 300  # метрики старше 5 минут → "5m ago"


# region FUNC_compute_staleness
def compute_staleness(generated_at: str | None) -> str | None:
    """Compute staleness of metrics data. Returns None if fresh, string description if stale.

    # ▶ ┌generated_at (ISO 8601)┐ → ◇ None? → ⎋ None
    #                               → ◇ age > 5 min? → ⎋ "Xm Ys" description
    #                               → ⎋ None (fresh)
    """
    if not generated_at:
        return None

    # ruff: ignore[PLW0717] — try мутирует параметр функции без возврата — извлечение теряет мутацию
    try:
        if generated_at.endswith("Z"):
            generated_at = generated_at[:-1] + "+00:00"
        gen_time = datetime.fromisoformat(generated_at)
        now = datetime.now(timezone.utc)
        delta = now - gen_time
        if delta.total_seconds() > _STALE_SECONDS:  # 5 minutes
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            return f"{minutes}m {seconds}s"
    except (ValueError, TypeError) as exc:
        # 170 W2-A2 (B3): silent swallow → warning с repr входа; контракт сохранён (fallback None).
        # Канон файла — LDD print(stderr) (не logger; research-B9 status-page).
        print(
            f"[IMP:7][compute_staleness] Invalid generated_at={generated_at!r} — treating as fresh: {exc}",
            file=sys.stderr,
        )
        return None

    return None


# endregion FUNC_compute_staleness
