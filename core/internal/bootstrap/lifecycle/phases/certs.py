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
##   3. SSL provision via helpers_domains.ssl_provision_via_orchestrator (unified entrypoint);
##      фаза интерпретирует статус: provisioned|converged → done, skipped_import|error →
##      done_with_warnings (P0 2026-08-27 — тихий import-skip НЕ маскируется).
## @changes  2026-08-27 · P0 — фаза интерпретирует статус ssl_provision_via_orchestrator
##           (skipped_import → False/done_with_warnings, converged → True)
## @rationale E3: phases.py 1080 LOC → доменные модули. certs-фазы — acme/ssl-домен.
## @changes  2026-08-02 · DevPlan 119 E3 — экстракция из lifecycle/phases.py
# endregion MODULE_CONTRACT
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable

# DevPlan 118 C6: единый путь litellm-config.yml — shared/llm_paths (литерал удалён).
# B3: канонический node-configs base — shared/deploy_paths (литерал /opt/node-configs удалён)
from core.internal.shared.exceptions import (
    ConfigNotFoundError,
)

logger = logging.getLogger(__name__)

# ── Import helpers from lifecycle/helpers (public I/O API, односторонняя зависимость) ──

from core.internal.bootstrap.lifecycle.helpers import domains as helpers_domains

# W1-A1 (план 170): timeout=120 литерал (install-acme.sh) → канон SoT LIFECYCLE_CMD_TIMEOUT (120,
# lifecycle-команда фазы) — AMBER-зачистка research-D §D1.
from core.internal.shared.timeouts import LIFECYCLE_CMD_TIMEOUT


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
            timeout=LIFECYCLE_CMD_TIMEOUT,
            env=clean_env,
            check=False,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][install_acme] acme.sh installed successfully")
            return True
        logger.warning(
            "[IMP:7][install_acme] acme.sh install failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][install_acme] acme.sh install timed out")
        return False
    except FileNotFoundError as e:
        logger.warning("[IMP:7][install_acme] Command not found: %s", e)
        return False
    else:
        return False


# endregion FUNC__install_acme


# region FUNC_phase_certificates
## @purpose φ7: SSL certificate provisioning — install acme.sh DNS-01 client, then provision
##           certificates for ALL domains (platform + projects) via cert_orchestrator.
##           Corresponds to init steps: install_acme, ssl_provision.
## @io      ⇥ core_dir, node_name, node_yaml, helpers: object | None (DI-канон 163 W-H,
##           DevPlan 167 D3 — helper-namespace для _install_acme/ssl_provision_via_orchestrator)
##           → ⎋ bool
## @complexity O(D * T) where D = domain count, T = cert issuance timeout
## @invariants
##   - acme.sh installation is non-fatal (best-effort)
##   - SSL provision is handled by _ssl_provision_via_orchestrator (unified cert entrypoint)
##   - All domains from node.yaml are processed (platform + all projects)
## 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · helper-namespace для I/O-функций фазы (167 D3)
## · Rejected: прямой module-level вызов _install_acme/helpers_domains.ssl_provision_via_orchestrator
## · Reason: seam = тестируемость реального вызова (тест передаёт helpers-namespace вместо
## ·   monkeypatch.setattr(certs_mod, "_install_acme"/helpers_domains.ssl_provision_via_orchestrator);
## ·   прод — module fallback без изменений)
## · Rev: если I/O-функции станут методами объекта-провайдера — helpers заменится его инстансом
def phase_certificates(
    core_dir: str,
    node_name: str,  # ruff: ignore[ARG001] — единый интерфейс фаз (core_dir, node_name, node_yaml)
    node_yaml: str,
    *,
    helpers: object | None = None,
) -> bool:
    """φ7: Certificates — install acme.sh, provision SSL for all domains.

    Pre-check: node.yaml exists (needed for domain extraction).
    Execute: install acme.sh → SSL provision via cert_orchestrator.
    Post-check: certificates issued (best-effort, cert_orchestrator handles S3 cache).
    """
    if not node_yaml or not os.path.isfile(node_yaml):
        msg = f"node.yaml not found: {node_yaml} — cannot provision certificates"
        raise ConfigNotFoundError(msg)

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
    install_acme = _resolve_helper(helpers, "_install_acme", _install_acme)
    ssl_provision = _resolve_helper(
        helpers, "ssl_provision_via_orchestrator", helpers_domains.ssl_provision_via_orchestrator
    )
    try:
        acme_ok = install_acme(core_dir)
        if acme_ok:
            logger.info("[IMP:9][phase:certificates] acme.sh installed/verified")
        else:
            logger.warning("[IMP:7][phase:certificates] acme.sh installation returned non-success (non-fatal)")
    # ruff: ignore[BLE001] — acme.sh install best-effort (широкий спектр subprocess/сети)
    except Exception as e:  # noqa: EXC — non-fatal: acme.sh is best-effort
        logger.warning("[IMP:7][phase:certificates] acme.sh installation failed (non-fatal): %s", e)

    # ── 2. SSL provision via cert_orchestrator (P0-честность: skipped-import ≠ done) ──
    # Контракт ssl_provision_via_orchestrator: "provisioned"/"converged" → успех;
    # "skipped_import"/"error" → done_with_warnings (фаза перевыполнится на резюме — это ровно
    # семантика восстановления при импорт-скипе с непровижнеными сертами).
    # ⚠️ TRAP[BUG] · 2026-08-27 · P0 · тихий import-skip маскировал отказ φ7
    # · Symptom: cert_orchestrator not importable при холодном bootstrap → helper возвращал None →
    # ·   здесь логировалось «SSL certificates provisioned for all domains» и фаза mark DONE —
    # ·   провижининга НЕ БЫЛО (S3-кеш пуст = restore-first мёртв при DR).
    # · Root: return None не различал «успех» и «тихий skip» — фаза не могла отличить.
    # · Fix: helper возвращает статус; skipped_import (импорт недоступен + серты НЕ на диске) →
    # ·   False → done_with_warnings → resume перевыполнит фазу после полной доставки core.
    # · Prevention: фаза интерпретирует фактический статус, а не отсутствие исключения.
    try:
        ssl_status = ssl_provision(core_dir, node_yaml)
    # ruff: ignore[BLE001] — SSL provision best-effort — S3/ACME API широкий спектр
    except Exception as e:  # noqa: EXC — non-fatal: SSL provisioning is best-effort (S3 cache fallback)
        logger.warning("[IMP:7][phase:certificates] SSL provision failed (non-fatal): %s", e)
        non_fatal_issues = True
    else:
        if ssl_status in {"provisioned", "converged"}:
            logger.info("[IMP:9][phase:certificates] SSL certificates provisioned for all domains")
        else:
            logger.warning(
                "[IMP:7][phase:certificates] SSL provision incomplete (status=%s) — certificates NOT provisioned",
                ssl_status,
            )
            non_fatal_issues = True

    if non_fatal_issues:
        logger.info("[IMP:8][phase:certificates] Complete with non-fatal issues")
        return False

    logger.info("[IMP:9][phase:certificates] φ7 complete — certificates provisioned")
    return True


# endregion FUNC_phase_certificates


# region FUNC_resolve_helper
## @purpose  DI-канон 163 W-H (helper-namespace, DevPlan 167 D3): вернуть helper из
##            injected namespace, иначе module-level fallback. None → fallback
##            (прод-поведение без изменений).
## @io       ⇥ helpers: object | None, name: str, fallback → ⎋ callable
## @complexity O(1) — getattr / fallback
def _resolve_helper(helpers: object | None, name: str, fallback: Callable[..., object]) -> Callable[..., object]:
    """DI helper-namespace resolver: injected helper wins, else module-level fallback."""
    if helpers is None:
        return fallback
    return getattr(helpers, name, fallback)


# endregion FUNC_resolve_helper
