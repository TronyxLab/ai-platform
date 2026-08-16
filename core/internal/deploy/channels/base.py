"""
Shared delivery-channel primitives: Payload, DeliveryResult, DeliveryChannel ABC, retry wrapper.
"""
# GREP_SUMMARY: delivery-channels, base, payload, delivery-result, abc, deliver, timeout, retry, runner, di-seam
# STRUCTURE: ▶ Payload dataclass → DeliveryResult dataclass → DeliveryChannel ABC (runner DI-seam) → _retry_deliver (2 retries × exponential backoff)
# region MODULE_CONTRACT
## @purpose  DeliveryChannel ABC core: Payload/DeliveryResult dataclasses, abstract channel
##           contract with runner DI-seam and _retry_deliver retry wrapper. Submodule of the
##           channels/ package (W4-B1 decomposition of the former channels.py monolith).
## @scope    Imported by channels/scp.py, channels/forced.py, channels/local.py and re-exported
##           from channels/__init__.py. Consumer-facing names (Payload, DeliveryResult,
##           DeliveryChannel) keep their import path via the package re-export.
## @invariants
##   1. Payload must contain at minimum tar_path and project_name
##   2. All channels have configurable timeout (default 600s) via PLATFORM_DEPLOY_TIMEOUT env var
##   3. Retry: 2 retries + exponential backoff (initial 5s, factor 2×)
##   4. DeliveryResult always has duration_s populated
##   5. Runner DI-seam (self._run) — subprocess.run по умолчанию, тесты инжектят fake runner
## @rationale DevPlan 089 DD1: ABC allows adding third channel (HTTP push for serverless)
##            without changing DeployOrchestrator. W4-B1 (план 170): base.py — общий слой
##            пакета channels/, переезд функции-в-функцию из channels.py (514 LOC).
## @changes 2026-07-30 | DevPlan 089 T1/T2/T3 — Created (в channels.py)
## @changes 2026-08-15 | план 170 W4-B1 — вынесен в channels/base.py (декомпозиция монолита)
## @changes 2026-08-16 | DevPlan 177 W3.1 — _retry_deliver → делегат shared/retry.py
##           (retry-loop/backoff/sleep консолидированы; +sleep_fn DI-шов в __init__)
# endregion MODULE_CONTRACT

from __future__ import annotations

import abc
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core.internal.shared.app_config import AppConfig
from core.internal.shared.exceptions import ConfigValidationError
from core.internal.shared.retry import retry as _shared_retry
from core.internal.shared.timeouts import (
    DEPLOY_TIMEOUT,
    RETRY_BACKOFF_SECONDS,
    RETRY_COUNT,
)

logger = logging.getLogger(__name__)

# ── Defaults (DevPlan 116 B5 T1/T7: значения — из единого реестра shared/timeouts.py) ──
# W4a (DevPlan 160 T4.1): DEFAULT_DEPLOY_TIMEOUT — ЧИСТАЯ константа (import-time env убран).
# Env PLATFORM_DEPLOY_TIMEOUT резолвится ЛЕНИВО на конструировании канала
# (DeliveryChannel.__init__ → AppConfig.from_env().deploy_timeout) — shell-фасады не ломаются.
DEFAULT_DEPLOY_TIMEOUT = DEPLOY_TIMEOUT
DEFAULT_RETRY_COUNT = RETRY_COUNT
# Канал использует RETRY_BACKOFF_SECONDS[0] (5s) с factor 2 (экспоненциальный backoff [5, 10]);
# retry-цикл — shared/retry.py (DevPlan 177 W3.1, см. _retry_deliver)
DEFAULT_RETRY_BACKOFF = RETRY_BACKOFF_SECONDS[0]


# region DATACLASSES


@dataclass
class Payload:
    """Payload to be delivered via a DeliveryChannel.

    ## @purpose — Encapsulates all data needed for a deploy delivery.
    ## @io — ⇥ constructor params → ⎋ Payload instance
    ## @complexity — O(1)
    """

    tar_path: Path
    project_name: str
    version: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tar_path or not self.project_name:
            msg = "Payload requires tar_path and project_name"
            raise ConfigValidationError(msg)


@dataclass
class DeliveryResult:
    """Result of a payload delivery operation.

    ## @purpose — Track delivery outcome, timing, and error details.
    ## @io — ⇥ constructor params → ⎋ DeliveryResult instance
    ## @complexity — O(1)
    """

    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_s: float = 0.0
    error_message: str | None = None


# endregion DATACLASSES


# region CLASS_DeliveryChannel (ABC)


class DeliveryChannel(abc.ABC):
    """Abstract base for delivery channels.

    ## @purpose — Define contract for all delivery channels.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N depends on channel implementation
    ## @invariants
    ##   - deliver() must always return a DeliveryResult (never raise)
    ##   - Timeout applies to the entire deliver() call
    ##   - Retries are handled internally by deliver()
    """

    def __init__(
        self,
        timeout: int | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,  # DI (177 W3.1): backoff-sleep fake; None = time.sleep
    ):
        # W4a: ленивый env-фолбэк (None → AppConfig.from_env().deploy_timeout) —
        # env PLATFORM_DEPLOY_TIMEOUT по-прежнему применяется, но на момент конструирования.
        self.timeout = timeout if timeout is not None else AppConfig.from_env().deploy_timeout
        # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · runner-инъекция (CommandRunner-seam, 167 D2)
        # · Rejected: тест патчил channels.subprocess.run строковым monkeypatch
        # · Reason: seam = тестируемость реального вызова — fake runner наблюдает ssh_cmd
        # ·   (R5-negative L-8: quoting-инъекция) без глобального os-патча
        # · Rev: при выносе каналов на другой transport-runner — синхронизировать протокол
        self._run: Callable[..., subprocess.CompletedProcess[str]] = runner if runner is not None else subprocess.run
        # 177 W3.1: DI-шов sleep для _retry_deliver (тесты инжектят мгновенный fake —
        # 0 реального backoff-sleep; None → time.sleep в shared.retry)
        self._sleep_fn: Callable[[float], None] | None = sleep_fn
        # Атрибут: динамически устанавливается orchestrator_cli/reconciler_projects
        # (host/user/key_file defaults). Фактически не читается deliver() (payload.metadata —
        # канон канала); декларация типизирует динамический атрибут (reportAttributeAccessIssue).
        self.metadata_defaults: dict[str, str] = {}

    @abc.abstractmethod
    def deliver(self, payload: Payload) -> DeliveryResult:
        """Deliver a payload through this channel.

        Args:
            payload: Payload to deliver.

        Returns:
            DeliveryResult with status and timing.
        """
        ...

    def _retry_deliver(self, payload: Payload) -> DeliveryResult:
        """Deliver with retry logic (2 retries + exponential backoff) via shared.retry.

        Args:
            payload: Payload to deliver.

        Returns:
            DeliveryResult from the last attempt.
        """

        # 177 W3.1: retry-loop/backoff/sleep/logging — в shared.retry (result-mode);
        # адаптер переводит исключения канала в failed DeliveryResult (1:1 семантика)
        # и трекает duration_s. Dead-ветка «No attempts made» удалена — attempts ≥ 1
        # гарантирует ≥1 итерации (эквивалент range(1 + DEFAULT_RETRY_COUNT)).
        def _attempt() -> DeliveryResult:
            start = time.monotonic()
            try:
                result = self.deliver(payload)
            except (subprocess.CalledProcessError, OSError, TimeoutError) as e:
                logger.error(
                    "[IMP:10][_retry_deliver][exception] Deliver exception for %s: %s",
                    payload.project_name,
                    e,
                )
                return DeliveryResult(
                    success=False,
                    stderr=str(e),
                    exit_code=-1,
                    duration_s=time.monotonic() - start,
                    error_message=str(e),
                )
            result.duration_s = time.monotonic() - start
            return result

        # Экспоненциальный backoff канала: [5, 10] — RETRY_BACKOFF_SECONDS[0] (5s) с factor 2
        # (DevPlan 116 B5 T7 — источник значений — shared/timeouts.py; 177 W3.1 — цикл в shared.retry)
        return _shared_retry(
            _attempt,
            attempts=1 + DEFAULT_RETRY_COUNT,  # 2 ретрая + первая попытка = 3
            backoff_seconds=[DEFAULT_RETRY_BACKOFF * (2**i) for i in range(DEFAULT_RETRY_COUNT)],
            retryable=lambda result: not result.success,
            sleep_fn=self._sleep_fn,
        )


# endregion CLASS_DeliveryChannel (ABC)
