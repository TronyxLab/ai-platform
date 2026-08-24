#!/usr/bin/env python3
# GREP_SUMMARY: domains-helpers, import-deploy-context, extract-domains, ssl-provision, cert-orchestrator, direct-import, context-deployer
# STRUCTURE: ▶ import_deploy_context ┌direct-import context_deployer.deploy_context (non-fatal)┐ → ⚡ extract_domains ┌extract_domains_for_context (public, CS-1)┐ → ⚡ ssl_provision_via_orchestrator ┌cert_orchestrator.orchestrate_certs┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Domain/deploy-context I/O-хелперы bootstrap-фаз — извлечены из state_machine
##           (B9 T1, U-08). Все функции публичные.
## @scope    domains.py: import_deploy_context, extract_domains, ssl_provision_via_orchestrator.
##           Используются phases.py (φ7 certificates, φ8 deploy_services, φ12 deploy_update).
## @invariants
##   - Прямые guarded-импорты deploy/cert модулей (T3.5, A5-прецедент): недоступность →
##     None + WARN, best-effort (DEPLOY_BEST_EFFORT), никогда не fatal
##   - importlib by-path (spec_from_file_location + sys.modules-регистрация) — ЗАПРЕЩЁН
##     (DEP-0018); модули загружаются ТОЛЬКО системой импорта — единая идентичность модуля
##   - extract_domains использует ПУБЛИЧНУЮ extract_domains_for_context (T3, CS-1)
##   - ssl_provision_via_orchestrator: context="" = все домены (platform + projects)
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
from pathlib import Path

# 142 W2: secrets.env → persistent /var/lib/platform/run (резолвер shared/deploy_paths)
from core.internal.shared import deploy_paths

logger = logging.getLogger(__name__)

# ── T3.5: importlib-обход удалён → обычные guarded-импорты (A5-прецедент, context_deployer.py:995) ──
# Направление bootstrap→deploy легально (core/AGENTS.md G3: bootstrap оркестрирует деплой, φ8).
# Guarded-import сохраняет best-effort семантику (DEPLOY_BEST_EFFORT): недоступность deploy/cert
# модулей на неполном core-деплое → WARN + skip шага, никогда не fatal.
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
    from core.internal.bootstrap.deploy.context_deployer import (
        deploy_context,
        extract_domains_for_context,
    )
except ImportError:
    orchestrate_certs = None  # type: ignore[assignment] — guarded import (best-effort)
    deploy_context = None  # type: ignore[assignment]
    extract_domains_for_context = None  # type: ignore[assignment]
    logger.warning("[IMP:7][domains] deploy/cert modules unavailable — best-effort steps will skip")


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
## @io       ⇥ core_dir: str, node_yaml: str → ⎋ None
## @complexity O(D * T) where D = domains, T = timeout per operation
## @invariants
##   - _source_secrets_env() is called inside cert_orchestrator for WEBNAMES_API_KEY
##   - S3 credentials are read directly by s3_ssl_cache from os.environ — no subshell
##   - context="" means ALL domains (no filtering)
##   - orchestrate_certs — обычный guarded-импорт (T3.5), единый module-инстанс
## @rationale DevPlan 052 §4.1: Replace _ssl_provision() with cert_orchestrator
##           to fix subshell credential loss and handle all domains (not just platform).
def ssl_provision_via_orchestrator(core_dir: str, node_yaml: str) -> None:
    """Provision SSL certs via cert_orchestrator (unified entrypoint)."""
    if orchestrate_certs is None:
        logger.warning("[IMP:7][ssl_provision] cert_orchestrator not importable — skipping (best-effort)")
        return
    bootstrap_dir = Path(core_dir) / "internal" / "bootstrap"

    # Extract ALL domains (platform + all projects, no context filter) via context_deployer
    context = ""  # empty = no filtering, all domains
    domains = extract_domains(core_dir, node_yaml, context)

    if not domains:
        logger.warning("[IMP:7][ssl_provision] No domains found in node.yaml — skipping")
        return

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


# endregion FUNC_ssl_provision_via_orchestrator
