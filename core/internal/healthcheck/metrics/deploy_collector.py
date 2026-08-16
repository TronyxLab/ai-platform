# GREP_SUMMARY: deploy-collector status-metrics deploy-status audit-jsonl last-deploy duration success SLO
# STRUCTURE: ▶ get_deploy_status ┌read_audit_log → filter deploy:* → last┐ → ⊕ DeployStatus {last_deploy_at, success, duration_s} → ⎋ unknown-дефолт
# region MODULE_CONTRACT
## @purpose  Deploy-метрики для status-metrics.json (170 W12 C5 SLO-минимум): последний
##           deploy-аудит (tag deploy:*) из /var/log/platform/audit.jsonl → поля
##           last_deploy_at/success/duration_s. Потребитель — platform_export_metrics (секция
##           "deploy") → status-page /metrics (Prometheus text) → SLO-recording rules.
## @scope    Только чтение audit.jsonl (shared/audit_logger.read_audit_log — единый reader).
##           НЕ пишет, НЕ мутирует. Вызывается в try/except coordinator'ом (не блокирует экспорт).
## @invariants
##   - Нет deploy-записей → {"status": "unknown"} (НЕ фейл экспорта)
##   - success = status последней записи not in {"FAILED", "ERROR", "WARN"}
##   - duration_s — float | None (extra-поле duration_s последней deploy-записи)
## @rationale SLO-минимум 170 W12 C5: deploy_success/duration — recording rules + burn-rate
##            alert; источник — уже существующий audit.jsonl (никакого нового канала записи).
## @changes  2026-08-15 | DevPlan 170 W12 C5 — Created
# endregion MODULE_CONTRACT

from typing import TypedDict, cast

from core.internal.shared.audit_logger import read_audit_log

# ⚠️ TRAP[DECISION] · 2026-08-15 · — · success-маппинг status → bool · Rejected: парсинг
# result-поля per-operation (deploy:deploy/deploy:rollback различаются) · Reason: status —
# нормализованный код записи (DEPLOYED/DONE/PARTIAL/SKIPPED/FAILED/ERROR/WARN); WARN —
# частичный успех → success=False для burn-rate консервативно (partial = несчастливый релиз)
# · Rev: если появится deploy-op со status, ломающим маппинг — расширить таблицу.


class DeployStatus(TypedDict, total=False):
    """Deploy-секция status-metrics.json (W12 C5)."""

    last_deploy_at: str
    success: bool
    duration_s: float
    status: str


_FAIL_STATUSES: frozenset[str] = frozenset({"FAILED", "ERROR", "WARN"})


def get_deploy_status(log_file: str = "/var/log/platform/audit.jsonl", limit: int = 200) -> DeployStatus:
    """Last deploy-audit entry → {last_deploy_at, success, duration_s} | {status: unknown}.

    ## @purpose  SLO-источник: последняя запись tag=deploy:* из audit.jsonl.
    ## @io       ⇥ log_file, limit → ⎋ DeployStatus (unknown при отсутствии записей)
    ## @complexity O(L) — read_audit_log читает хвост (~limit записей)
    """
    entries = read_audit_log(log_file=log_file, limit=limit)
    last: dict[str, object] | None = None
    for e in entries:
        tag = cast("object", e.get("tag"))
        if isinstance(tag, str) and tag.startswith("deploy:"):
            last = e
    if last is None:
        return {"status": "unknown"}

    status = cast("str", last.get("status", "UNKNOWN"))
    ts = cast("str", last.get("ts", ""))
    duration_raw = cast("object", last.get("duration_s"))
    duration_s: float | None = float(duration_raw) if isinstance(duration_raw, (int, float)) else None

    result: DeployStatus = {
        "last_deploy_at": ts,
        "success": status not in _FAIL_STATUSES,
        "status": status,
    }
    if duration_s is not None:
        result["duration_s"] = duration_s
    return result
