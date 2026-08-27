#!/usr/bin/env python3
# GREP_SUMMARY: domains-helpers, import-deploy-context, extract-domains, ssl-provision, cert-orchestrator, direct-import, context-deployer
# STRUCTURE: ▶ import_deploy_context ┌direct-import context_deployer.deploy_context (non-fatal)┐ → ⚡ extract_domains ┌extract_domains_for_context (public, CS-1)┐ → ⚡ ssl_provision_via_orchestrator ┌cert_orchestrator.orchestrate_certs│skipped_import│converged-check disk┐ → ⎋ str (provisioned|converged|skipped_import|error)
# region MODULE_CONTRACT
## @purpose  Domain/deploy-context I/O-хелперы bootstrap-фаз — извлечены из state_machine
##           (B9 T1, U-08). Все функции публичные.
## @scope    domains.py: import_deploy_context, extract_domains, ssl_provision_via_orchestrator.
##           Используются phases.py (φ7 certificates, φ8 deploy_services, φ12 deploy_update).
## @invariants
##   - Прямые guarded-импорты deploy/cert модулей (T3.5, A5-прецедент): недоступность →
##     None + best-effort (DEPLOY_BEST_EFFORT), никогда не fatal. НО импорт-скип НЕ тихий:
##     IMP:10 критический лог с причиной (_import_fail_ctx) + статус skipped_import из
##     ssl_provision_via_orchestrator (P0 2026-08-27: тихий skip маскировал отказ φ7)
##   - ssl_provision_via_orchestrator возвращает СТАТУС: provisioned|converged|skipped_import|error
##     (фаза интерпретирует: skipped_import/error → done_with_warnings → resume перевыполнит)
##   - importlib by-path (spec_from_file_location + sys.modules-регистрация) — ЗАПРЕЩЁН
##     (DEP-0018); модули загружаются ТОЛЬКО системой импорта — единая идентичность модуля
##   - extract_domains использует ПУБЛИЧНУЮ extract_domains_for_context (T3, CS-1)
##   - ssl_provision_via_orchestrator: context="" = все домены (platform + projects)
## @changes  2026-08-27 · P0 (маскирование отказа φ7) — ssl_provision_via_orchestrator → статус
##           provisioned|converged|skipped_import|error; import-скип → IMP:10 + skipped_import;
##           converged-проверка по диску (fullchain.pem, letsencrypt_live()); per-module
##           guarded-импорты с _import_fail_ctx (частичная причина ImportError)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
##           T3.5: скрытое runtime-ребро lifecycle→deploy через importlib становится
##           статическим (ARCH-303/DEP-0018); двойная идентичность модуля (dotted + shadow
##           top-level имя) — источник P1 RC 121 → класс устранён.
## @changes  2026-08-01 · Extracted from state_machine (B9 T1); _extract_domains_for_context →
##           публичная extract_domains_for_context (CS-1)
## @changes  2026-08-22 · T3.5 — importlib-обход удалён → обычные guarded-импорты
##           (spec_from_file_location/sys.modules убраны, −40 LOC; прецедент — A5 в context_deployer)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import traceback
from pathlib import Path

# 142 W2: secrets.env → persistent /var/lib/platform/run (резолвер shared/deploy_paths)
from core.internal.shared import deploy_paths

logger = logging.getLogger(__name__)


# region FUNC__import_fail_ctx
## @purpose  Зафиксировать ЧАСТИЧНУЮ причину ImportError guarded-импорта (P0-честность,
##           2026-08-27): последняя строка traceback одной строкой — следующий прогон видит
##           контекст отказа (какой модуль/какой вложенный импорт упал), вместо тихого skip.
## @io       ⇥ module: str (имя модуля для ctx=) → ⎋ None (только IMP:10 критический лог)
## @complexity O(1) — format_exc + лог
## @invariants
##   - Вызывается ТОЛЬКО внутри except ImportError (format_exc валиден)
##   - Никогда не raise — лог-хелпер не должен ломать импорт модуля
def _import_fail_ctx(module: str) -> None:
    """Log the last traceback line of a guarded-import failure (IMP:10, non-silent)."""
    last_line = traceback.format_exc().strip().splitlines()[-1]
    logger.critical("[IMP:10][domains] import-fail ctx=%s: %s", module, last_line)


# endregion FUNC__import_fail_ctx


# ── T3.5: importlib-обход удалён → обычные guarded-импорты (A5-прецедент, context_deployer.py:995) ──
# Направление bootstrap→deploy легально (core/AGENTS.md G3: bootstrap оркестрирует деплой, φ8).
# Guarded-import сохраняет best-effort семантику (DEPLOY_BEST_EFFORT): недоступность deploy/cert
# модулей на неполном core-деплое → НЕ fatal. Однако импорт-скип больше НЕ тихий: IMP:10
# критический лог с причиной (см. _import_fail_ctx) + статус skipped_import из
# ssl_provision_via_orchestrator — фаза НЕ рапортует done, пока выпуск не выполнен и серты
# не на диске (P0 2026-08-27: тихий skip маскировал отказ φ7 → S3-кеш пуст = restore-first мёртв).
# ⚠️ TRAP[BUG] · 2026-08-03 · P1 · sys.modules-регистрация до exec_module (RC 121 прод φ7/φ8) — КЛАСС устранён T3.5
# · Symptom: "'NoneType' object has no attribute '__dict__'" — dataclasses._is_type читает
# ·   sys.modules[cls.__module__]; без регистрации модуля ДО exec_module — dataclass-декораторы
# ·   внутри context_deployer/cert_orchestrator падали.
# · Root: importlib-обход системы импорта (spec_from_file_location) создавал shadow-идентичность
# ·   модуля (двойной code object: dotted + top-level имя) — state расщеплялся, isinstance/
# ·   dataclass-механика ломались на cross-file объектах.
# · Fix (T3.5): importlib-обход удалён → обычные guarded-импорты; sys.modules-регистрация
# ·   больше не нужна — класс двойной идентичности устранён.
# · Prevention: модули загружаются ТОЛЬКО системой импорта; importlib by-path — запрещён (DEP-0018).
try:
    from core.internal.bootstrap.cert_orchestrator import orchestrate_certs
except ImportError:
    orchestrate_certs = None  # type: ignore[assignment] — guarded import (best-effort)
    _import_fail_ctx("cert_orchestrator")

try:
    from core.internal.bootstrap.deploy.context_deployer import (
        deploy_context,
        extract_domains_for_context,
    )
except ImportError:
    deploy_context = None  # type: ignore[assignment]
    extract_domains_for_context = None  # type: ignore[assignment]
    _import_fail_ctx("context_deployer")


# region FUNC_import_deploy_context
## @purpose  Run context_deployer.deploy_context() (нормальный импорт, T3.5). Non-fatal.
## @io       ⇥ core_dir: str, node_name: str, node_yaml: str → ⎋ None (non-fatal)
## @complexity O(D * P) where D = domains, P = projects
def import_deploy_context(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Run context_deployer.deploy_context() via normal import (T3.5) — best-effort."""
    if deploy_context is None:
        logger.warning("[IMP:7][deploy_context] context_deployer not importable — skipping (best-effort)")
        return
    try:
        result = deploy_context(core_dir, node_name, node_yaml)
        logger.info(
            "[IMP:9][deploy_context] Complete: deployed=%d skipped=%d failed=%d",
            result.deployed if result else 0,
            result.skipped if result else 0,
            result.failed if result else 0,
        )
    # ruff: ignore[BLE001] — deploy_context runtime-ошибки произвольного модуля (best-effort)
    except Exception as e:  # noqa: EXC — non-fatal: deploy_context is best-effort
        logger.warning("[IMP:7][deploy_context] deploy_context failed (non-fatal): %s", e)


# endregion FUNC_import_deploy_context


# region FUNC_extract_domains
## @purpose  Extract domains via context_deployer.extract_domains_for_context (публичная, CS-1).
## @io       ⇥ core_dir: str, node_yaml: str, context: str → ⎋ list[str]
## @complexity O(N) YAML parse
def extract_domains(core_dir: str, node_yaml: str, context: str) -> list[str]:
    """Extract domains via context_deployer.extract_domains_for_context."""
    # T3.5: core_dir удержан для стабильности публичного API (CS-1, контракт-тест пиннит сигнатуру) —
    # при обычном импорте путь к context_deployer.py не строится (загрузка через систему импорта).
    del core_dir
    if extract_domains_for_context is None:
        logger.warning("[IMP:7][ssl_provision] context_deployer not importable — no domains")
        return []
    try:
        return extract_domains_for_context(node_yaml, context)
    # ruff: ignore[BLE001] — extract никогда не fatal (DEPLOY_BEST_EFFORT policy)
    except Exception as e:  # noqa: EXC — catch-all for extraction (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.warning("[IMP:7][ssl_provision] Failed to extract domains: %s", e)
    return []


# endregion FUNC_extract_domains


# region FUNC_ssl_provision_via_orchestrator
## @purpose  Unified cert orchestration via cert_orchestrator.orchestrate_certs().
##           Контракт возврата (P0-честность, 2026-08-27 — маскирование отказа φ7):
##           "provisioned"     — оркестрация выполнена (orchestrate_certs отработал без исключений);
##           "converged"       — выпуск НЕ выполнялся, но состояние сходится: серты уже на диске
##                               для всех доменов ИЛИ доменов нет (выпускать нечего);
##           "skipped_import"  — orchestrate_certs недоступен (guarded-import) и серты НЕ на диске
##                               (или домены неопределимы) → фаза должна вернуть done_with_warnings,
##                               чтобы перевыполниться на резюме;
##           "error"           — оркестрация упала (best-effort: non-fatal, фаза — done_with_warnings).
## ⚠️ TRAP[BUG] · 2026-08-27 · P0 · тихий import-skip маскировал отказ φ7 certificates
## · Symptom: cert_orchestrator not importable при холодном bootstrap → WARN + return None →
## ·   фаза логировала «SSL certificates provisioned for all domains» и mark DONE — провижининга
## ·   НЕ БЫЛО (серты позже выпустил другой процесс; S3-кеш остался пуст = restore-first мёртв при DR).
## · Root: helper возвращал None и при реальном отказе (skip), и при успехе — фаза не могла
## ·   отличить; return None трактовался как success (тихий skip ≠ error).
## · Fix: helper возвращает статус (provisioned|converged|skipped_import|error); import-скип —
## ·   IMP:10 критический лог + skipped_import; converged-проверка по диску (fullchain.pem)
## ·   не наказывает повтором issuance уже выпущенные серты; фаза → done_with_warnings.
## · Prevention: I/O-хелпер сигнализирует ФАКТИЧЕСКОЕ состояние, фаза не делает вид «сделано».
## @io       ⇥ core_dir: str, node_yaml: str → ⎋ str (provisioned|converged|skipped_import|error)
## @complexity O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - _source_secrets_env() is called inside cert_orchestrator for WEBNAMES_API_KEY
##   - S3 credentials are read directly by s3_ssl_cache from os.environ — no subshell
##   - context="" means ALL domains (no filtering)
##   - orchestrate_certs — обычный guarded-импорт (T3.5), единый module-инстанс
##   - Import-скип БОЛЬШЕ НЕ тихий: IMP:10 критический лог (см. _import_fail_ctx)
##   - Converged-проверка: /etc/letsencrypt/live/{domain}/fullchain.pem через канонический
##     резолвер deploy_paths.letsencrypt_live() (C7), не литерал
## @rationale DevPlan 052 §4.1: Replace _ssl_provision() with cert_orchestrator
##           to fix subshell credential loss and handle all domains (not just platform).
def ssl_provision_via_orchestrator(core_dir: str, node_yaml: str) -> str:
    """Provision SSL certs via cert_orchestrator (unified entrypoint).

    Returns "provisioned" | "converged" | "skipped_import" | "error" (см. @purpose).
    """
    # ── Import-unavailable path (P0, 2026-08-27): НЕ тихий skip — IMP:10 + статус для фазы ──
    if orchestrate_certs is None:
        logger.critical(
            "[IMP:10][ssl_provision] cert_orchestrator NOT importable — SSL issuance SKIPPED (phase must NOT report done)"
        )
        converged = _certs_converged_on_disk(core_dir, node_yaml)
        if converged is True:
            # Серты уже на диске (или доменов нет) — выпуск не наказывается повтором issuance.
            return "converged"
        # converged is None (домены неопределимы — context_deployer тоже недоступен) или False
        # (серты НЕ на диске) → skipped_import: фаза вернёт done_with_warnings → перевыполнится
        # на резюме, когда core доедет целиком и импорт заработает.
        return "skipped_import"
    bootstrap_dir = Path(core_dir) / "internal" / "bootstrap"

    # Extract ALL domains (platform + all projects, no context filter) via context_deployer
    context = ""  # empty = no filtering, all domains
    domains = extract_domains(core_dir, node_yaml, context)

    if not domains:
        logger.warning("[IMP:7][ssl_provision] No domains found in node.yaml — skipping")
        return "converged"

    # W3.5-1 (164): issue_cert.py — Python-модуль (subprocess-канал `python3 -m core.internal.bootstrap.issue_cert`,
    # диспетч в cert_orchestrator._issue_cert по суффиксу .sh/.py). Прежний issue-cert.sh удалён.
    # v1.0.1 TRAP[BUG]: cert_orchestrator (170 W7) ожидает str (endswith(".sh")) — Path давал
    # «'PosixPath' object has no attribute 'endswith'» → φ7 certificates done_with_warnings.
    issue_cert_script = str(Path(bootstrap_dir) / "issue_cert.py")
    secrets_env = os.environ.get("SECRETS_ENV_FILE", str(deploy_paths.secrets_env_file()))

    try:
        cert_result = orchestrate_certs(domains, issue_cert_script, secrets_env, migrate_cron=True)
        logger.info("[IMP:9][ssl_provision] Cert orchestration complete: %s", cert_result.to_dict())
    # ruff: ignore[BLE001] — orchestration runtime-ошибки произвольного модуля (best-effort)
    except Exception as e:  # noqa: EXC — non-fatal: SSL is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][ssl_provision] Cert orchestration failed (non-fatal): %s", e)
        return "error"
    else:
        return "provisioned"


# endregion FUNC_ssl_provision_via_orchestrator


# region FUNC__certs_converged_on_disk
## @purpose  Проверка конвергенции при недоступном orchestrate_certs: каждый домен из node.yaml
##           уже имеет fullchain.pem на диске (/etc/letsencrypt/live/{domain}/fullchain.pem) —
##           дёшево (один isfile на домен), не наказывает повторным issuance'ом уже выпущенные
##           серты (P0-семантика восстановления: resume НЕ должен перевыпускать готовое).
## @io       ⇥ core_dir: str, node_yaml: str → ⎋ bool | None
##              (True = все серты на диске / доменов нет; False = есть домены без сертов;
##               None = домены неопределимы — extract_domains_for_context недоступен)
## @complexity O(D) — D = число доменов (по одному isfile на домен)
## @invariants
##   - Пустой список доменов → True (выпускать нечего — состояние сходится)
##   - extract_domains_for_context None → None (нельзя ни подтвердить, ни опровергнуть —
##     консервативно; фаза перевыполнится на резюме)
##   - LE live dir — канонический резолвер deploy_paths.letsencrypt_live() (C7), не литерал
def _certs_converged_on_disk(core_dir: str, node_yaml: str) -> bool | None:
    """Return True if every node.yaml domain already has fullchain.pem on disk (converged)."""
    if extract_domains_for_context is None:
        logger.warning("[IMP:7][ssl_provision] context_deployer unavailable — convergence cannot be verified")
        return None
    domains = extract_domains(core_dir, node_yaml, "")
    if not domains:
        return True  # no domains → nothing to issue → converged
    le_live = deploy_paths.letsencrypt_live()
    missing = [d for d in domains if not (le_live / d / "fullchain.pem").is_file()]
    if missing:
        logger.info("[IMP:8][ssl_provision] Certificates missing on disk for: %s", ", ".join(missing))
        return False
    logger.info(
        "[IMP:9][ssl_provision] All %d domain(s) already have fullchain.pem on disk — converged",
        len(domains),
    )
    return True


# endregion FUNC__certs_converged_on_disk
