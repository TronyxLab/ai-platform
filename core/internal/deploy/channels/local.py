"""
LocalChannel — no-op delivery channel for payloads already present at the target (VPS-side receive).
"""
# GREP_SUMMARY: delivery-channels, local, no-op, receive, vps-side, payload, deliver
# STRUCTURE: ▶ deliver: log "already in place" → ⎋ DeliveryResult(success=True) — network-free no-op
# region MODULE_CONTRACT
## @purpose  LocalChannel: no-op transport for DeployOrchestrator.receive() — payload tar was
##           already extracted to projects_base/<project>/ before deploy() is called. Keeps the
##           DeployOrchestrator pipeline (compose-up → healthcheck → snapshot → audit) intact
##           on the VPS without a self-SSH transport hop. Submodule of channels/ package (W4-B1).
## @scope    VPS-side receive delivery (context_deployer, receive_flow, DeployOrchestrator.receive).
##           Re-exported from channels/__init__.py.
## @invariants
##   1. deliver() never touches the network
##   2. Always succeeds (payload placement is the caller's responsibility)
##   3. Retry wrapper (_retry_deliver) degenerates to a single no-op call
## @rationale DevPlan 095 E2E exposed that receive() used SCPChannel with empty metadata —
##            deliver() always failed. LocalChannel is the minimal contract-compliant replacement
##            (no self-SSH dependency). W4-B1: перенесён из channels.py без изменений.
## @changes 2026-07-31 | DevPlan 095 T16 — Created (в channels.py)
## @changes 2026-08-15 | план 170 W4-B1 — вынесен в channels/local.py
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from core.internal.deploy.channels.base import DeliveryChannel, DeliveryResult, Payload

logger = logging.getLogger(__name__)

# 🧐 TRAP[DECISION] · 2026-07-31 · HI · LocalChannel — VPS-side receive delivery
# · Rejected: SCPChannel() with empty metadata in DeployOrchestrator.receive()
#   (bug: deliver ALWAYS failed with "SCPChannel requires 'host' in payload.metadata" —
#   receive() ran the compose engine through a transport channel that cannot work locally;
#   exposed by DevPlan 095 E2E T16 on a real VPS)
# · Reason: receive() already extracted the payload to /opt/projects/<name>/ — a transport
#   channel is meaningless there. LocalChannel is a contract-compliant no-op delivery that
#   lets the full DeployOrchestrator pipeline run (compose up → healthcheck → snapshot →
#   audit) on the VPS side. Alternative rejected: self-SSH (root@127.0.0.1) — requires the
#   VPS root key to authorize itself, unreliable on fresh nodes.
# · Rev: if a real "deliver locally" semantic is needed (e.g., remote-dir override),
#   extend LocalChannel with a local copy step instead of a transport.


# region CLASS_LocalChannel


class LocalChannel(DeliveryChannel):
    """Delivery channel for payloads already present at the target location (VPS-side receive).

    ## @purpose — No-op transport for DeployOrchestrator.receive(): the payload tar was
    ##            already extracted to projects_base/<project>/ before deploy() is called.
    ##            Keeps the DeployOrchestrator pipeline (compose-up → healthcheck →
    ##            snapshot → audit) intact on the VPS without a self-SSH transport hop.
    ## @io — ⇥ Payload → ⎋ DeliveryResult(success=True) — files already in place
    ## @complexity — O(1)
    ## @invariants
    ##   - deliver() never touches the network
    ##   - Always succeeds (payload placement is the caller's responsibility)
    ##   - Retry wrapper (_retry_deliver) degenerates to a single no-op call
    ## @rationale DevPlan 095 E2E exposed that receive() used SCPChannel with empty
    ##            metadata — deliver() always failed. LocalChannel is the minimal
    ##            contract-compliant replacement (no self-SSH dependency).
    """

    # ruff: ignore[PLR6301]  # метод-контракт: реализация абстрактного DeliveryChannel.deliver (интерфейс канала)
    def deliver(self, payload: Payload) -> DeliveryResult:
        logger.info(
            "[IMP:9][LocalChannel][deliver] Local delivery — payload for %s already in place (tar=%s)",
            payload.project_name,
            payload.tar_path.name,
        )
        return DeliveryResult(
            success=True,
            stdout="local delivery — payload already extracted",
            exit_code=0,
            duration_s=0.0,
        )


# endregion CLASS_LocalChannel
