#!/usr/bin/env python3
# GREP_SUMMARY: converge-ssl, reconcile-ssl-certs, r-ssl, cert-restore, ssl-provision, restore-first, letsencrypt, s3-cache, verify-vhosts-prereq, cache-drill
# STRUCTURE: ▶ R-ssl reconcile_ssl_certs ┌preview (dry_run/report_only): on-disk check — НЕ мутирует┐ │ ⚡ ssl_provision_via_orchestrator (restore-first disk→S3→issue) → ◇ provisioned→mutated(+exit1) │ converged→no-op │ skipped_import|error→fail(+exit2) ┐ → ⎋ drift entry {R-ssl}
# region MODULE_CONTRACT
## @purpose  R-ssl reconcile_ssl_certs — самолечение отсутствующих SSL-сертификатов в converge:
##           restore-first (disk → S3 → issue) через domains.ssl_provision_via_orchestrator ПЕРЕД
##           R6 verify_vhosts. F-02 приёмо-сдаточной валидации (cache-drill C2): удалён live-серт →
##           converge падал на R6 «cannot load certificate .../fullchain.pem» (exit 2) БЕЗ попытки
##           восстановления — restore-first жил только в bootstrap-фазах (φ7/φ12), не в converge.
##           Извлечён в доменный модуль converge/ (SRP-паттерн R1-R11, B9 T2 U-31).
## @scope    converge/ssl_certs.py: reconcile_ssl_certs. Вызывается оркестратором reconciler.py
##           (unit_actions: R5 → R-ssl → R6 — строго ПЕРЕД verify_vhosts).
## @invariants
##   - РЕЖИМЫ: preview (dry_run/report_only) — НЕ мутирует: on-disk convergence check через
##     domains.ssl_certs_converged_on_disk (status: converged | mutated-WOULD | warn-undeterminable);
##     converge (мутация) — domains.ssl_provision_via_orchestrator (идемпотентен: живые серты →
##     "converged" → no-op; повторный converge на здоровых сертах = SKIP-семантика)
##   - Статус-маппинг (P0-честность domains.py, 2026-08-27):
##     "provisioned"      → report mutated + set_exit(1) — дрейф устранён, мутация применена
##                          (паттерн R1/R8; exit 1 = warnings, НЕ ошибка)
##     "converged"        → report converged — no-op, без set_exit
##     "skipped_import" | "error" → report fail + set_exit(2) — НЕ проглатывается (exit 2 канон)
##   - ssl_provision_via_orchestrator вызывается с (core_dir, node_yaml) — канон-сигнатура
##   - preview НЕ вызывает ssl_provision_via_orchestrator (mode-контракт reconciler «no mutations»)
##   - Импорт-направление: converge (bootstrap-слой) → lifecycle.helpers.domains (bootstrap-слой) —
##     легально; цикла НЕТ (domains → shared/cert_orchestrator/deploy.context_deployer, НЕ converge)
## 🧐 TRAP[DECISION] · 2026-09-02 · — · Маппинг статусов R-ssl: "provisioned"→mutated, "converged"→converged
## · Rejected: "provisioned"→converged / "converged"→mutated (позиционное прочтение требования
## ·   «report converged/mutated соответственно») — семантически инвертировано: "converged" =
## ·   выпуск НЕ выполнялся, серты уже на диске → мутаций НЕТ; "provisioned" = серты восстановлены
## ·   из S3/выпущены → мутация ЕСТЬ. Словопорядок требования — оговорка.
## · Reason: консистентность с R1/R8 (mutated → set_exit(1)) и domains.py контрактом статусов
## · Rev: если приёмо-сдаточная валидация F-02 трактует provisioned иначе — ренейм маппинга в одном месте
## @rationale Q: Почему R-ssl отдельным R-юнитом, а не хуком в начале R6?
##            A: (1) data-driven dispatch (T2.17) — таблица unit_actions единообразна, юнит виден
##            в --units-фильтре и JSON-отчёте как отдельное измерение дрейфа; (2) restore-first —
##            оркестрация сертификатов (S3/ACME) — отдельная доменная ответственность от nginx
##            vhost-интегрити; (3) при R6-хуке порядок мутации и проверки спутан в одной функции.
##            Q: Почему preview-ветка использует on-disk проверку вместо ssl_provision?
##            A: --dry-run/--report-only — режимы без мутаций (инвариант reconciler); ssl_provision
##            реально восстанавливает серты → preview обязан лишь ОТЧИТАТЬ дрейф (WOULD-fix),
##            как R1 dry-run («would get ug+x»).
## @changes  2026-09-02 · F-02 (cache-drill C2, tronyx-vps) — Created
## @links    core/internal/bootstrap/lifecycle/helpers/domains.py (ssl_provision_via_orchestrator,
##           ssl_certs_converged_on_disk), core/internal/bootstrap/converge/reconciler.py (dispatch),
##           core/internal/bootstrap/converge/vhosts.py (R6 verify_vhosts)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.bootstrap.converge.infra import report_add, set_exit
from core.internal.bootstrap.lifecycle.helpers import domains

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# R-ssl — reconcile_ssl_certs
# ═══════════════════════════════════════════════════════════════════
# region FUNC_reconcile_ssl_certs
## @purpose  Restore-first конвергенция SSL-сертификатов: серт удалён с диска → восстановить
##           из S3-кеша (или выпустить), ДО проверки vhost'ов (R6) — nginx -t не должен падать
##           на «cannot load certificate». Идемпотентен: серты на диске → "converged" → no-op.
## @io       ⇥ core_dir: str (путь к core/), node_yaml_path: str (путь к node.yaml),
##              dry_run/report_only: bool (preview — без мутаций)
##           → ⎋ drift entry dict {"unit":"R-ssl","status":"...","detail":"..."}
## @complexity O(D * T) — D = домены, T = таймаут restore/issue на домен (делегирование)
## @invariants
##   - preview (dry_run|report_only): НЕ вызывает ssl_provision_via_orchestrator;
##     on-disk конвергенция через domains.ssl_certs_converged_on_disk:
##       True  → converged (no-op); False → mutated (WOULD restore, set_exit(1));
##       None  → warn (undeterminable — экстрактор недоступен, set_exit(1))
##   - converge: статус domains.ssl_provision_via_orchestrator маппится 1:1 (см. MODULE_CONTRACT)
##   - skipped_import/error → fail + set_exit(2) — НЕ молча проглатывается (P0-честность)
## @rationale Маппинг повторяет контракт статусов domains.ssl_provision_via_orchestrator
##            (P0 2026-08-27) — converge НЕ изобретает собственной классификации.
def reconcile_ssl_certs(
    core_dir: str,
    node_yaml_path: str,
    *,
    dry_run: bool = False,
    report_only: bool = False,
) -> dict[str, str]:
    """Reconcile SSL certificate convergence (restore-first) before R6 vhost verification.

    Returns a drift entry dict with status: converged|mutated|fail|warn.
    """
    unit = "R-ssl"
    preview = bool(dry_run or report_only)
    logger.info(
        "[IMP:8][converge][%s] START: reconcile_ssl_certs — restore-first cert convergence (disk → S3 → issue)",
        unit,
    )

    # ── Preview (dry_run/report_only): mode-контракт «no mutations» ──
    # ssl_provision_via_orchestrator РЕАЛЬНО восстанавливает/выпускает серты — в preview
    # (R1-паттерн: «WOULD fix ... with chmod ug+x») отчитываем дрейф по on-disk проверке.
    if preview:
        logger.info(
            "[IMP:9][converge][%s] DRY-RUN/REPORT-ONLY: мутации НЕ выполняются — on-disk convergence check",
            unit,
        )
        converged = domains.ssl_certs_converged_on_disk(core_dir, node_yaml_path)
        if converged is True:
            logger.info("[IMP:9][converge][%s] SKIP: All certs on disk — converged [dry-run]", unit)
            entry = {"unit": unit, "status": "converged", "detail": "All certs on disk [dry-run]"}
            report_add(unit, "converged", "All certs on disk [dry-run]")
        elif converged is False:
            logger.info(
                "[IMP:9][converge][%s] WOULD restore missing SSL certs (restore-first: disk → S3 → issue) [dry-run]",
                unit,
            )
            entry = {"unit": unit, "status": "mutated", "detail": "WOULD restore missing SSL certs [dry-run]"}
            report_add(unit, "mutated", "WOULD restore missing SSL certs [dry-run]")
            set_exit(1)
        else:
            logger.warning(
                "[IMP:8][converge][%s] WARN: cert convergence undeterminable in preview (extractor unavailable) [dry-run]",
                unit,
            )
            entry = {
                "unit": unit,
                "status": "warn",
                "detail": "cert convergence undeterminable (extractor unavailable) [dry-run]",
            }
            report_add(unit, "warn", "cert convergence undeterminable (extractor unavailable) [dry-run]")
            set_exit(1)
        return entry

    # ── Converge (мутация): restore-first через cert-оркестратор ──
    result = domains.ssl_provision_via_orchestrator(core_dir, node_yaml_path)
    if result == "provisioned":
        logger.info("[IMP:9][converge][%s] DONE: SSL certs provisioned via orchestrator (restore-first)", unit)
        entry = {
            "unit": unit,
            "status": "mutated",
            "detail": "SSL certs provisioned via orchestrator (restore-first)",
        }
        report_add(unit, "mutated", "SSL certs provisioned via orchestrator (restore-first)")
        set_exit(1)
    elif result == "converged":
        logger.info("[IMP:9][converge][%s] SKIP: All certs converged — no issuance needed (no-op)", unit)
        entry = {"unit": unit, "status": "converged", "detail": "All certs converged (no issuance needed)"}
        report_add(unit, "converged", "All certs converged (no issuance needed)")
    else:
        # skipped_import | error — P0-честность domains.py: НЕ проглатывать (ошибки → exit 2)
        logger.error(
            "[IMP:10][converge][%s] FAIL: SSL cert restore failed (status=%s) — vhost verification may fail",
            unit,
            result,
        )
        entry = {"unit": unit, "status": "fail", "detail": f"SSL cert restore failed: {result}"}
        report_add(unit, "fail", f"SSL cert restore failed: {result}")
        set_exit(2)

    return entry


# endregion FUNC_reconcile_ssl_certs
