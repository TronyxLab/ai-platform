# GREP_SUMMARY: verify-sweep, report, text-table, verdict-table, e2e-verify-pass-fail, sweep-render
# STRUCTURE: ▶ _render_text_report ┌SweepReport┐ → ⊕ строки таблицы (fqdn | HTTP | TLS | итог) → ⊕ collect errors → ∑ exit → ⎋ str
# region MODULE_CONTRACT
## @purpose  Рендер текстового отчёта sweep-верификации (DevPlan 136 AC W5: «таблица =
##           вывод команды»): endpoint → HTTP code → TLS вердикт → итог (OK/FAIL).
## @scope    Только _render_text_report (человекочитаемая таблица). JSON-сериализация —
##           в SweepReport.to_dict (models.py); выбор канала вывода — в main() (__init__.py).
## @invariants
##   - Одна строка на endpoint: fqdn | HTTP code | TLS вердикт | итог
##   - Collection errors печатаются отдельным блоком (R4: FAIL-причина видима)
##   - Итоговая строка с exit-семантикой (✅ PASS / ❌ FAIL)
##   - Чистая функция: не пишет в stdout/stderr (вывод — ответственность main)
## @rationale Декомпозиция монолита verify_sweep.py (план 170 W7-E1, research-A §7):
##            отчёт выделен отдельно от CLI/логики проверок — чистый рендер без сайд-эффектов.
## @changes  2026-08-15 | План 170 W7-E1 — выделено из verify_sweep.py (чистый move)
## @usecases
##   - main() (__init__.py): print(_render_text_report(report), file=sys.stderr) при не-json прогоне
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.verify_sweep.models import SweepReport

logger = logging.getLogger(__name__)


# region FUNC_render_text_report
def render_text_report(report: SweepReport) -> str:
    """Текстовый отчёт-таблица (endpoint → HTTP → TLS → вердикт).

    ▶ ┌report┐ → ⊕ строки таблицы → ∑ verdict → ⎋ str

    ## @purpose — Человекочитаемая таблица sweep (AC W5: «таблица = вывод команды»).
    ##            Публичное имя (без _): __init__.py re-export'ит как приватный алиас
    ##            _render_text_report (private-imports гейт U-07: from X import name as _alias).
    ## @io — ⇥ report: SweepReport → ⎋ str (многострочная таблица)
    ## @complexity — O(E) где E = endpoints
    ## @invariants
    ##   - Одна строка на endpoint: fqdn | HTTP code | TLS verdict | итог
    ##   - Collection errors печатаются отдельным блоком (R4: FAIL-причина видима)
    ##   - Итоговая строка с exit-семантикой
    """
    lines: list[str] = []
    lines.append("")
    lines.append("┌─ e2e-verify sweep ──────────────────────────────────────────┐")
    lines.append(f"│ node={report.node}  mode={report.mode}  endpoints={report.endpoints}")
    lines.append("├────────────────────────────────────────────────────────────┤")
    for r in report.http:
        http_v = f"HTTP {r.code}" if r.code is not None else f"ERR {r.error}"
        tls_row = next((t for t in report.tls if t.fqdn == r.fqdn), None)
        tls_v = (
            "TLS fail"
            if tls_row and not tls_row.ok
            else ("TLS warn" if tls_row and tls_row.verdict == "warn" else "TLS ok")
        )
        overall = "OK" if r.ok and (tls_row is None or tls_row.ok) else "FAIL"
        lines.append(f"│ {r.fqdn:<32} {http_v:<18} {tls_v:<10} {overall}")
    lines.extend(f"│ COLLECT FAIL: {err}" for err in report.collect_errors)
    lines.append("└────────────────────────────────────────────────────────────┘")
    lines.append("")
    if report.exit_code == 0:
        lines.append(f"✅ e2e-verify PASS — {report.endpoints} endpoint(s) all green")
    else:
        lines.append(f"❌ e2e-verify FAIL — {report.endpoints} endpoint(s), review table above")
    return "\n".join(lines)


# endregion FUNC_render_text_report
