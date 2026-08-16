"""
Delivery Channel abstractions for DeployOrchestrator — SCPChannel and ForcedCommandChannel.
Package re-export: former channels.py monolith (514 LOC) decomposed into base/scp/forced/local
(W4-B1, план 170). Import path `from core.internal.deploy.channels import ...` stays valid.
"""
# GREP_SUMMARY: delivery-channels, abc, scp, rsync, forced-command, ssh, payload, deliver, timeout, retry, auth, local
# STRUCTURE: ▶ channels/__init__ → re-export base (Payload/DeliveryResult/ABC/_retry_deliver) + scp (SCPChannel) + local (LocalChannel) + forced (ForcedCommandChannel) → ⎋ единый импорт-фасад
# region MODULE_CONTRACT
## @purpose  DeliveryChannel ABC with SCPChannel, LocalChannel and ForcedCommandChannel implementations.
##           Each channel delivers a Payload (tar_path, project_name, version, metadata)
##           and returns DeliveryResult (success, stdout, stderr, exit_code, duration_s).
##           Пакет-фасад: re-export'ит ВСЕ публичные имена бывшего channels.py — импорт-путь
##           `from core.internal.deploy.channels import ...` сохраняется без правок импортеров.
## @scope    Used by DeployOrchestrator to abstract delivery mechanism. SCPChannel for
##           bootstrap/rsync delivery, LocalChannel for VPS-side receive (payload already
##           in place), ForcedCommandChannel for CI/tar+SSH forced-command.
## @invariants
##   1. Payload must contain at minimum tar_path and project_name
##   2. All channels have configurable timeout (default 600s) via PLATFORM_DEPLOY_TIMEOUT env var
##   3. Retry: 2 retries + exponential backoff (initial 5s, factor 2×)
##   4. Auth: SSH key-based only (no password auth)
##   5. SCPChannel uses SSH agent forwarding
##   6. DeliveryResult always has duration_s populated
##   7. __init__.py re-export'ит ВСЕ публичные имена бывшего channels.py (моно-контракт):
##      Payload, DeliveryResult, DeliveryChannel, SCPChannel, LocalChannel, ForcedCommandChannel,
##      DEFAULT_DEPLOY_TIMEOUT, DEFAULT_RETRY_COUNT, DEFAULT_RETRY_BACKOFF
## @rationale DevPlan 089 DD1: ABC allows adding third channel (HTTP push for serverless)
##            without changing DeployOrchestrator. Two existing channels (SCP, forced-command)
##            have fundamentally different lifecycles. W4-B1 (план 170, research-A §3): channels.py
##            514 LOC → пакет channels/ {base, scp, forced, local}; __init__ — единственная
##            точка re-export — импортеры (orchestrator, orchestrator_cli, context_deployer,
##            reconciler_projects, payload_deliverer, receive_flow + 11 тест-файлов) не меняются.
## @changes 2026-07-30 | DevPlan 089 T1/T2/T3 — Created (channels.py)
## @changes 2026-08-15 | план 170 W4-B1 — channels.py → пакет channels/ (base/scp/forced/local),
##                      __init__ re-export всех публичных имён; поведение 1:1
# endregion MODULE_CONTRACT

from core.internal.deploy.channels.base import (
    DEFAULT_DEPLOY_TIMEOUT,
    DEFAULT_RETRY_BACKOFF,
    DEFAULT_RETRY_COUNT,
    DeliveryChannel,
    DeliveryResult,
    Payload,
)
from core.internal.deploy.channels.forced import ForcedCommandChannel
from core.internal.deploy.channels.local import LocalChannel
from core.internal.deploy.channels.scp import SCPChannel

__all__ = [
    "DEFAULT_DEPLOY_TIMEOUT",
    "DEFAULT_RETRY_BACKOFF",
    "DEFAULT_RETRY_COUNT",
    "DeliveryChannel",
    "DeliveryResult",
    "ForcedCommandChannel",
    "LocalChannel",
    "Payload",
    "SCPChannel",
]
