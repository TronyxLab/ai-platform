#!/usr/bin/env python3
# GREP_SUMMARY: domains-helpers, import-deploy-context, extract-domains, ssl-provision, cert-orchestrator, importlib, context-deployer
# STRUCTURE: ▶ import_deploy_context ┌importlib context_deployer.deploy_context (non-fatal)┐ → ⚡ extract_domains ┌extract_domains_for_context (public, CS-1)┐ → ⚡ ssl_provision_via_orchestrator ┌cert_orchestrator.orchestrate_certs┐ → ⎋
# region MODULE_CONTRACT
## @purpose  Domain/deploy-context I/O-хелперы bootstrap-фаз — извлечены из state_machine
##           (B9 T1, U-08). Все функции публичные.
## @scope    domains.py: import_deploy_context, extract_domains, ssl_provision_via_orchestrator.
##           Используются phases.py (φ7 certificates, φ8 deploy_services, φ12 deploy_update).
## @invariants
##   - Динамическая загрузка через importlib (deploy/cert модули могут отсутствовать на ранних
##     фазах bootstrap) — best-effort, никогда не fatal
##   - extract_domains использует ПУБЛИЧНУЮ extract_domains_for_context (T3, CS-1)
##   - ssl_provision_via_orchestrator: context="" = все домены (platform + projects)
## @rationale Strangler-Fig: извлечение I/O из state_machine-монолита (DevPlan 116 B9 D1).
## @changes  2026-08-01 · Extracted from state_machine (B9 T1); _extract_domains_for_context →
##           публичная extract_domains_for_context (CS-1)
# endregion MODULE_CONTRACT

from __future__ import annotations

import importlib.util
import logging
import os
import sys

logger = logging.getLogger(__name__)


# region FUNC_import_deploy_context
## @purpose  Import and run context_deployer.deploy_context() via importlib. Non-fatal.
## @io       ⇥ core_dir: str, node_name: str, node_yaml: str → ⎋ None (non-fatal)
## @complexity O(D * P) where D = domains, P = projects
def import_deploy_context(core_dir: str, node_name: str, node_yaml: str) -> None:
    """Import context_deployer.deploy_context() via importlib and execute."""
    try:
        deployer_path = os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py")
        spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)
        if spec and spec.loader:
            deployer_mod = importlib.util.module_from_spec(spec)
            # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · sys.modules до exec_module (RC 121 прод φ7/φ8)
            # · Symptom: "'NoneType' object has no attribute '__dict__'" — dataclasses._is_type
            #   читает sys.modules[cls.__module__]; без регистрации модуля ДО exec_module —
            #   dataclass-декораторы внутри context_deployer падают.
            # · Fix: регистрация в sys.modules перед exec (паттерн cert_orchestrator).
            sys.modules["context_deployer"] = deployer_mod
            spec.loader.exec_module(deployer_mod)
            result = deployer_mod.deploy_context(core_dir, node_name, node_yaml)
            logger.info(
                "[IMP:9][deploy_context] Complete: deployed=%d skipped=%d failed=%d",
                result.deployed if result else 0,
                result.skipped if result else 0,
                result.failed if result else 0,
            )
        else:
            logger.warning("[IMP:7][deploy_context] Cannot load context_deployer.py")
    except Exception as e:  # noqa: EXC — non-fatal: deploy_context is best-effort
        logger.warning("[IMP:7][deploy_context] deploy_context failed (non-fatal): %s", e)


# endregion FUNC_import_deploy_context


# region FUNC_extract_domains
## @purpose  Extract domains via context_deployer.extract_domains_for_context (публичная, CS-1).
## @io       ⇥ core_dir: str, node_yaml: str, context: str → ⎋ list[str]
## @complexity O(N) YAML parse
def extract_domains(core_dir: str, node_yaml: str, context: str) -> list[str]:
    """Extract domains via context_deployer.extract_domains_for_context."""
    try:
        deployer_path = os.path.join(core_dir, "internal", "bootstrap", "deploy", "context_deployer.py")
        spec = importlib.util.spec_from_file_location("context_deployer", deployer_path)
        if spec and spec.loader:
            deployer_mod = importlib.util.module_from_spec(spec)
            # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · sys.modules до exec_module (RC 121 прод φ7)
            # · Symptom: "'NoneType' object has no attribute '__dict__'" — dataclasses._is_type
            #   читает sys.modules[cls.__module__] (см. import_deploy_context TRAP).
            sys.modules["context_deployer"] = deployer_mod
            spec.loader.exec_module(deployer_mod)
            return deployer_mod.extract_domains_for_context(node_yaml, context)
    except Exception as e:  # noqa: EXC — catch-all for importlib-based calls (best-effort: DEPLOY_BEST_EFFORT policy)
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
##   - Dynamic import allows cert_orchestrator to be updated independently
## @rationale DevPlan 052 §4.1: Replace _ssl_provision() with cert_orchestrator
##           to fix subshell credential loss and handle all domains (not just platform).
def ssl_provision_via_orchestrator(core_dir: str, node_yaml: str) -> None:
    """Provision SSL certs via cert_orchestrator (unified entrypoint)."""
    bootstrap_dir = os.path.join(core_dir, "internal", "bootstrap")

    # Extract ALL domains (platform + all projects, no context filter) via context_deployer
    context = ""  # empty = no filtering, all domains
    domains = extract_domains(core_dir, node_yaml, context)

    if not domains:
        logger.warning("[IMP:7][ssl_provision] No domains found in node.yaml — skipping")
        return

    issue_cert_script = os.path.join(bootstrap_dir, "issue-cert.sh")
    secrets_env = os.environ.get("SECRETS_ENV_FILE", "/run/platform/secrets.env")

    # Dynamic import of cert_orchestrator
    spec = importlib.util.spec_from_file_location(
        "cert_orchestrator",
        os.path.join(bootstrap_dir, "cert_orchestrator.py"),
    )
    if spec and spec.loader:
        cert_mod = importlib.util.module_from_spec(spec)
        sys.modules["cert_orchestrator"] = cert_mod
        spec.loader.exec_module(cert_mod)
        cert_result = cert_mod.orchestrate_certs(domains, issue_cert_script, secrets_env, migrate_cron=True)
        logger.info("[IMP:9][ssl_provision] Cert orchestration complete: %s", cert_result.to_dict())
    else:
        logger.warning("[IMP:7][ssl_provision] Cannot load cert_orchestrator.py")


# endregion FUNC_ssl_provision_via_orchestrator
