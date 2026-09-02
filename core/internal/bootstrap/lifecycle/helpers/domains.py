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
##   - extract_domains_for_context None (F-01, 2026-08-31) → skipped_import ДО extract_domains():
##     домены НЕОПРЕДЕЛИМЫ ≠ «доменов нет»; ПУСТОЙ список (экстрактор доступен) → converged
##   - importlib by-path (spec_from_file_location + sys.modules-регистрация) — ЗАПРЕЩЁН
##     (DEP-0018); модули загружаются ТОЛЬКО системой импорта — единая идентичность модуля
##   - extract_domains использует ПУБЛИЧНУЮ extract_domains_for_context (T3, CS-1)
##   - ssl_provision_via_orchestrator: context="" = все домены (platform + projects)
## @changes  2026-08-31 · P0 (F-01, asi-team-vps cold bootstrap) — extractor-unavailable НЕ
##           трактуется как converged: extract_domains_for_context is None → skipped_import
##           (ложный success φ7 → nginx crash-loop; B1 re-exec/B2 lazy-import — связанные фиксы)
## @changes  2026-08-27 · P0 (маскирование отказа φ7) — ssl_provision_via_orchestrator → статус
##           provisioned|converged|skipped_import|error; import-скип → IMP:10 + skipped_import;
##           converged-проверка по диску (fullchain.pem, letsencrypt_live()); per-module
##           guarded-импорты с _import_fail_ctx (частичная причина ImportError)
## @changes  2026-09-01 · strict-семантика деплоя контекста в INIT: import_deploy_context +strict.
##           φ8 (bootstrap INIT) — failed≠∅/исключение → IMP:10 + PlatformFatalError → фаза
##           failed в state.json (resumable, повтор доводит). φ12 (UPDATE) — strict=False,
##           best-effort сохранён (DEPLOY_BEST_EFFORT, D2 — WARN→0).
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
from core.internal.shared.exceptions import PlatformFatalError

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


# region FUNC__failed_project_names
## @purpose  Извлечь имена failed-проектов из deploy_context-результата (duck-typed:
##           .results[] с .status == "failed" → .name). Носитель failed-имён после агрегации
##           _step_deploy_projects + _step_vhosts (синтетическая запись "<vhost-render>").
## @io       ⇥ result: object (ContextDeployResult) → ⎋ list[str] (имена failed; [] если нет)
## @complexity O(R) — R = число проектов в result.results
## @invariants
##   - result без .results (None/не-list) → [] (деградация безопасна — strict-фейл всё
##     равно сработает по result.failed счётчику, без перечня имён)
def _failed_project_names(result: object) -> list[str]:
    """Return failed project names from a deploy_context result (duck-typed)."""
    results = getattr(result, "results", None)
    if not isinstance(results, list):
        return []
    names: list[str] = []
    for entry in results:
        if getattr(entry, "status", "") == "failed":
            name = getattr(entry, "name", None)
            if isinstance(name, str) and name:
                names.append(name)
    return names


# endregion FUNC__failed_project_names


# region FUNC_import_deploy_context
## @purpose  Run context_deployer.deploy_context() (нормальный импорт, T3.5). Non-fatal по
##           умолчанию (best-effort, DEPLOY_BEST_EFFORT — D2). strict=True (INIT, φ8):
##           result.failed≠0 ИЛИ исключение → IMP:10 + PlatformFatalError — критерий
##           приёмо-сдаточной валидации «конец bootstrap = все проекты контекста live».
## @io       ⇥ core_dir: str, node_name: str, node_yaml: str, strict: bool = False
##           → ⎋ None (strict=False, non-fatal) | raises PlatformFatalError (strict=True)
## @complexity O(D * P) where D = domains, P = projects
## @invariants
##   - strict=False: текущее поведение — сбой/исключение → WARN (non-fatal, DEPLOY_BEST_EFFORT)
##   - strict=True: result.failed≠0 → IMP:10 с перечнем failed-имён + PlatformFatalError
##   - strict=True: исключение из deploy_context → IMP:10 + PlatformFatalError (from e)
##   - deploy_context None (guarded-import) → WARN skip в ОБА режимах (импорт-скип НЕ fatal,
##     канон T3.5/DEPLOY_BEST_EFFORT; _import_fail_ctx уже зафиксировал причину на импорте)
## 🧐 TRAP[DECISION] · 2026-09-01 · — · INIT strict / UPDATE best-effort: φ8 strict=True, φ12 — False
## · Rejected: всегда-fatal — ломает φ12 DEPLOY_BEST_EFFORT контракт (D2, WARN→0)
## · Reason: критерий «конец bootstrap = все проекты live» (failed≠∅ = недоведённая нода)
## · Rev: если в init появятся легитимные failed (stub-only проекты) — переработать
## ·   классификацию failed vs skipped (awaiting_deploy ≠ failed)
def import_deploy_context(
    core_dir: str,
    node_name: str,
    node_yaml: str,
    *,
    strict: bool = False,
) -> None:
    """Run context_deployer.deploy_context() via normal import (T3.5) — best-effort unless strict."""
    if deploy_context is None:
        logger.warning("[IMP:7][deploy_context] context_deployer not importable — skipping (best-effort)")
        return
    try:
        result = deploy_context(core_dir, node_name, node_yaml)
    except Exception as e:  # noqa: EXC — non-fatal by default: deploy_context is best-effort
        # BLE001 не срабатывает: strict-ветка re-raise'ит (raise ... from e) — except не blind.
        if strict:
            logger.critical("[IMP:10][deploy_context] deploy_context failed (strict): %s", e)
            msg = f"Context deploy failed (strict, INIT): {e}"
            raise PlatformFatalError(msg) from e
        logger.warning("[IMP:7][deploy_context] deploy_context failed (non-fatal): %s", e)
    else:
        failed_count = result.failed if result else 0
        logger.info(
            "[IMP:9][deploy_context] Complete: deployed=%d skipped=%d failed=%d",
            result.deployed if result else 0,
            result.skipped if result else 0,
            failed_count,
        )
        if strict and failed_count:
            failed_names = _failed_project_names(result)
            logger.critical(
                "[IMP:10][deploy_context] STRICT FAIL (INIT): context deploy failed=%d: %s",
                failed_count,
                ", ".join(failed_names) or "<names unavailable>",
            )
            msg = (
                f"Context deploy failed (strict, INIT): {failed_count} project(s) failed: "
                f"{', '.join(failed_names) or '<names unavailable>'}"
            )
            raise PlatformFatalError(msg)


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
##           "provisioned"     — реальная мутация: issued>0 ИЛИ restored>0 (серт выпущен/восстановлен);
##           "converged"       — состояние сходится БЕЗ мутации: все домены skipped (уже валидны),
##                               серты уже на диске для всех доменов ИЛИ доменов нет;
##           "skipped_import"  — orchestrate_certs недоступен (guarded-import) и серты НЕ на диске
##                               (или домены неопределимы) → фаза должна вернуть done_with_warnings,
##                               чтобы перевыполниться на резюме;
##           "error"           — оркестрация упала ИЛИ failed>0 (best-effort: non-fatal,
##                               фаза — done_with_warnings; F-10: failed-домены больше не маскируются).
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

    # ── P0 (F-01, 2026-08-31): экстрактор НЕ доступен → домены НЕОПРЕДЕЛИМЫ, не «пусто» ──
    # orchestrate_certs импортировался, но context_deployer НЕ (pydantic-цепочка deploy/__init__ →
    # llm/__init__ → policy_schema на системном python3 3.12) → extract_domains() вернула бы []
    # и ветка ниже трактовала бы это как «доменов нет → converged» (ЛОЖНЫЙ success φ7: серты НЕ
    # выпущены → nginx crash-loop «cannot load certificate»). Консервативно: skipped_import →
    # фаза done_with_warnings → resume перевыполнит, когда импорт заработает (B1/B2, F-01).
    # ⚠️ TRAP[BUG] · 2026-08-31 · P0 · extractor-unavailable трактовался как converged (F-01)
    # · Symptom: orchestrate_certs импортируется, context_deployer НЕТ (ModuleNotFoundError:
    # ·   No module named 'pydantic' на системном python3 3.12) → extract_domains() → [] →
    # ·   return "converged" → φ7 «SSL certificates provisioned for all domains» (done) →
    # ·   серты НЕ выпущены → nginx crash-loop (cannot load certificate .../fullchain.pem).
    # · Root: ПУСТОЙ список доменов трактовался как «доменов нет (выпускать нечего)» вместо
    # ·   «домены НЕОПРЕДЕЛИМЫ» — helper не различал конфиг без доменов и недоступный экстрактор.
    # · Fix: явная проверка extract_domains_for_context is None ДО extract_domains() →
    # ·   "skipped_import" (не converged, не provisioned); различие сохранено: экстрактор доступен
    # ·   + доменов нет → converged (легитимный no-op, тест test_ssl_provision_no_domains_converged).
    # · Prevention: I/O-хелпер сигнализирует «не могу определить» отдельным статусом (skipped_import);
    # ·   фаза не делает вид «сделано» при неопределимом входе (контракт @purpose, P0 2026-08-27).
    if extract_domains_for_context is None:
        logger.critical(
            "[IMP:10][ssl_provision] context_deployer NOT importable — domains UNDETERMINABLE "
            "(phase must NOT report done)"
        )
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
        # F-10 (027): трёхветочный маппинг вместо безусловного "provisioned" —
        # безусловный статус рендерил R-ssl как mutated на КАЖДОМ no-op converge
        # (повторный node-update → converge_update done_with_warnings, rc=1).
        # ⚠️ TRAP[BUG] · 2026-09-02 · P2 · R-ssl always-mutated on no-op converge ·
        # Root: helper возвращал "provisioned" при любом успехе orchestrate_certs ·
        # Fix: failed>0 → error (честный warning, resume перевыполнит);
        # issued/restored>0 → provisioned (реальная мутация); иначе converged (no-op).
        if cert_result.failed > 0:
            logger.warning(
                "[IMP:7][ssl_provision] %d domain(s) failed during orchestration — reporting error",
                cert_result.failed,
            )
            return "error"
        if cert_result.issued > 0 or cert_result.restored > 0:
            return "provisioned"
        logger.info("[IMP:8][ssl_provision] All %d domain(s) already valid — converged (no-op)", len(domains))
        return "converged"


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


# region FUNC_ssl_certs_converged_on_disk
## @purpose  Публичная обёртка над _certs_converged_on_disk для НЕ-мутирующего preview-режима
##           (converge R-ssl dry_run/report_only): конвергенция проверяется по диску БЕЗ вызова
##           ssl_provision_via_orchestrator — mode-контракт reconciler «--dry-run/--report-only:
##           no mutations» (F-02, cache-drill C2). Вся логика остаётся в приватной функции
##           (DRY — одна точка истины по on-disk конвергенции).
## @io       ⇥ core_dir: str, node_yaml: str → ⎋ bool | None
##              (True = все серты на диске / доменов нет; False = есть домены без сертов;
##               None = домены неопределимы — extract_domains_for_context недоступен)
## @complexity O(D) — D = число доменов (делегирование)
## @invariants
##   - Делегирует в _certs_converged_on_disk (единый канон on-disk проверки)
##   - Сам вызов НЕ мутирует — только isfile-проверки
def ssl_certs_converged_on_disk(core_dir: str, node_yaml: str) -> bool | None:
    """Public non-mutating convergence check — preview mode (converge R-ssl dry-run/report-only)."""
    return _certs_converged_on_disk(core_dir, node_yaml)


# endregion FUNC_ssl_certs_converged_on_disk
