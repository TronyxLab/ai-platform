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
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — git clone acme.sh через tor-прокси
    # · Symptom: install-acme.sh падал/висел на свежей ноде (init: TOR_ENABLED=true → HTTP_PROXY
    # ·   из secrets.env в env cli.py → git clone через privoxy→tor → медленные/падающие цепи →
    # ·   run timeout 120s → фаза certificates done_with_warnings → deploy_services ЗАБЛОКИРОВАН →
    # ·   весь холодный бутстрап падает (сертификаты при этом выданы: S3 restore + wildcard).
    # · Root: install-acme.sh документирует «Proxy vars are expected to be clean at this stage
    # ·   (unset_platform_proxy already ran)» — но unset_platform_proxy живёт только в ЛОКАЛЬНОМ
    # ·   bootstrap.sh; REMOTE-цепочка (build_ssh_cmd → cli.py → source_secrets_env) кладёт
    # ·   HTTP_PROXY/HTTPS_PROXY в env процесса → subprocess наследует.
    # · Fix: вычистить proxy-переменные из env subprocess для install-acme.sh (контракт скрипта).
    # · Prevention: любой скрипт с документированным «proxy clean» контрактом вызывать с чистой env.
    clean_env = dict(os.environ)
    for proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        clean_env.pop(proxy_var, None)
    try:
        result = subprocess.run(
            ["bash", install_script],
            capture_output=True,
            text=True,
            timeout=120,
            env=clean_env,
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

    # ── 1. Install acme.sh (best-effort infra-инструмент, НЕ deliverable фазы) ──
    # ⚠️ TRAP[BUG] · 2026-08-06 · P1 · Ночная сессия 141 — acme-fail валил фазу → блок деплоя
    # · Symptom: acme.sh install fail (exit=1) → non_fatal_issues=True → фаза вернула False →
    # ·   done_with_warnings → dependency-гейт заблокировал deploy_services → весь cold bootstrap
    # ·   FAILED, хотя сертификаты ВСЕ выданы (S3 restore + wildcard *.tronyx.ru, summary: failed=0).
    # · Root: deliverable фазы = сертификаты (post-check), а статус фазы решался по инструменту.
    # · Fix: acme-инструмент — WARN-only; False возвращается ТОЛЬКО если провален ssl-provision
    # ·   (сам deliverable). Деградация renewal-cron при отсутствии acme.sh уже логируется
    # ·   оркестратором (IMP:7 «acme.sh not found — skipping cron install») и лечится node-update.
    # · Rev: если renewal-канал без acme.sh станет критичным — поднять до блокирующего.
    try:
        acme_ok = _install_acme(core_dir)
        if acme_ok:
            logger.info("[IMP:9][phase:certificates] acme.sh installed/verified")
        else:
            logger.warning("[IMP:7][phase:certificates] acme.sh installation returned non-success (non-fatal)")
    except Exception as e:  # noqa: EXC — non-fatal: acme.sh is best-effort
        logger.warning("[IMP:7][phase:certificates] acme.sh installation failed (non-fatal): %s", e)

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
