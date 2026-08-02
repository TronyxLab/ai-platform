#!/usr/bin/env python3
# GREP_SUMMARY: phases-certs, certificates, acme, ssl-provision, install-acme, cert-orchestrator, bootstrap-phase, E3
# STRUCTURE: ▶ certs-фазы (φ7) → ◇ install acme.sh → ◇ ssl_provision_via_orchestrator → ⊕ LDD logs → ⎋ bool
# region MODULE_CONTRACT
## @purpose  Certificates-domain bootstrap phase (DevPlan 119 E3) — φ7 phase_certificates + helper
##           _install_acme. Интерфейс (core_dir, node_name, node_yaml) -> bool сохранён.
## @scope    Consumed by lifecycle/phases/__init__.py (агрегатор) → state_machine.py execute_phase.
##           Извлечено из lifecycle/phases.py (DevPlan 119 E3, AUDIT-2 M3).
## @invariants
##   1. Phase is idempotent — safe to re-run on a provisioned node.
##   2. acme.sh installation is non-fatal (best-effort).
##   3. SSL provision via helpers_domains.ssl_provision_via_orchestrator (unified entrypoint).
## @rationale E3: phases.py 1080 LOC → доменные модули. certs-фазы — acme/ssl-домен.
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──
from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains


# region FUNC__install_acme
def _install_acme(core_dir: str) -> bool:
    """Install acme.sh for SSL provisioning (init only). Returns True on success.

    ## @purpose — Install acme.sh and DNS API extensions. Idempotent: skips if installed.
    ##            Moved from steps.py to phases.py per DevPlan 087 AC4 (no _step_* in steps.py).
    ## @io — ⇥ core_dir: platform core directory path → ⎋ bool (True = success)
    ## @complexity — O(1) + subprocess
    ## @invariants — Non-fatal: if install-acme.sh fails, log WARN and return False
    """
    install_script = os.path.join(core_dir, "internal", "bootstrap", "install-acme.sh")
    if not os.path.isfile(install_script):
        logger.warning("[IMP:7][install_acme] install-acme.sh not found at %s — skipping", install_script)
        return False

    logger.info("[IMP:9][install_acme] Installing acme.sh")
    try:
        result = subprocess.run(
            ["bash", install_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][install_acme] acme.sh installed successfully")
            return True
        logger.warning(
            "[IMP:7][install_acme] acme.sh install failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][install_acme] acme.sh install timed out")
        return False
    except FileNotFoundError as e:
        logger.warning("[IMP:7][install_acme] Command not found: %s", e)
        return False


# endregion FUNC__install_acme


# region FUNC_phase_certificates
## @purpose φ7: SSL certificate provisioning — install acme.sh DNS-01 client, then provision
##           certificates for ALL domains (platform + projects) via cert_orchestrator.
##           Corresponds to init steps: install_acme, ssl_provision.
## @io      ⇥ core_dir, node_name, node_yaml → ⎋ bool
## @complexity O(D * T) where D = domain count, T = cert issuance timeout
## @invariants
##   - acme.sh installation is non-fatal (best-effort)
##   - SSL provision is handled by _ssl_provision_via_orchestrator (unified cert entrypoint)
##   - All domains from node.yaml are processed (platform + all projects)
def phase_certificates(core_dir: str, node_name: str, node_yaml: str) -> bool:
    """φ7: Certificates — install acme.sh, provision SSL for all domains.

    Pre-check: node.yaml exists (needed for domain extraction).
    Execute: install acme.sh → SSL provision via cert_orchestrator.
    Post-check: certificates issued (best-effort, cert_orchestrator handles S3 cache).
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        raise ConfigNotFoundError(f"node.yaml not found: {node_yaml} — cannot provision certificates")

    non_fatal_issues = False

    # ── 1. Install acme.sh ──
    try:
        acme_ok = _install_acme(core_dir)
        if acme_ok:
            logger.info("[IMP:9][phase:certificates] acme.sh installed/verified")
        else:
            logger.warning("[IMP:7][phase:certificates] acme.sh installation returned non-success")
            non_fatal_issues = True
    except Exception as e:  # noqa: EXC — non-fatal: acme.sh is best-effort
        logger.warning("[IMP:7][phase:certificates] acme.sh installation failed (non-fatal): %s", e)
        non_fatal_issues = True

    # ── 2. SSL provision via cert_orchestrator ──
    try:
        helpers_domains.ssl_provision_via_orchestrator(core_dir, node_yaml)
        logger.info("[IMP:9][phase:certificates] SSL certificates provisioned for all domains")
    except Exception as e:  # noqa: EXC — non-fatal: SSL provisioning is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:certificates] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:certificates] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:certificates] φ7 complete — certificates provisioned")
    return True


# endregion FUNC_phase_certificates
