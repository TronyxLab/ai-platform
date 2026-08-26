"""
ForcedCommandChannel — SSH forced-command delivery channel (tar piped through stdin).
"""
# GREP_SUMMARY: delivery-channels, forced-command, ssh, payload, deliver, stdin, receive, dispatch, timeout, shlex-quote
# STRUCTURE: ▶ deliver: validate host → build ssh_cmd ("receive <project> <version>") → _send_forced (tar via stdin) → _receive_reply (interpret result) → ⎋ DeliveryResult
# region MODULE_CONTRACT
## @purpose  ForcedCommandChannel: deliver payload via SSH forced-command. Tar payload piped
##           through stdin to the VPS-side receiver. Submodule of channels/ package (W4-B1).
## @scope    Used by DeployOrchestrator for CI/tar+SSH forced-command delivery. Host/user/
##           key_file passed in payload.metadata. Re-exported from channels/__init__.py.
## @invariants
##   1. Host parameter passed in payload.metadata["host"]
##   2. SSH user from payload.metadata.get("user", "ci-deploy")
##   3. SSH key from payload.metadata.get("key_file", "~/.ssh/ci_deploy_key")
##   4. Remote command = "receive <project> <version>" — при forced-command на сервере эта
##      строка становится SSH_ORIGINAL_COMMAND для `orchestrator_cli dispatch` (DevPlan 116
##      B1 T2 D1). Версия (sha) берётся из payload.version (D5 — версия через аргументы).
##   5. T9.7 (L-8): project_name/version через shlex.quote (инъекция-защита ДО validate receive)
##   6. Ключи БЕЗ forced-command: ssh сам выполнит `receive ...` — команды нет в PATH →
##      понятная ошибка (документировано, T5: deliver — единственный операторский путь)
## @rationale DevPlan 089 T3: forced-command канал для CI. W4-B1: deliver (82 LOC)
##            декомпозирован на _send_forced/_receive_reply (поведение 1:1).
## @changes 2026-07-30 | DevPlan 089 T3 — Created (в channels.py)
## @changes 2026-08-15 | план 170 W4-B1 — вынесен в channels/forced.py, deliver декомпозирован
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from core.internal.deploy.channels.base import DeliveryChannel, DeliveryResult, Payload
from core.internal.shared.ssh_opts import SSH_OPTS

logger = logging.getLogger(__name__)


# region CLASS_ForcedCommandChannel


class ForcedCommandChannel(DeliveryChannel):
    """SSH forced-command delivery channel.

    ## @purpose — Deliver payload via SSH forced-command.
    ##            Tar payload is piped through stdin to the VPS-side receiver.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N = file size / transfer speed
    ## @invariants
    ##   - Host parameter passed in payload.metadata["host"]
    ##   - SSH user from payload.metadata.get("user", "ci-deploy")
    ##   - SSH key from payload.metadata.get("key_file", "~/.ssh/ci_deploy_key")
    ##   - Remote command = "receive <project> <version>" — при forced-command на сервере эта
    ##     строка становится SSH_ORIGINAL_COMMAND для `orchestrator_cli dispatch` (DevPlan 116
    ##     B1 T2 D1). Версия (sha) берётся из payload.version (D5 — версия через аргументы).
    ##   - Ключи БЕЗ forced-command: ssh сам выполнит `receive ...` — команды нет в PATH →
    ##     понятная ошибка (документировано, T5: deliver — единственный операторский путь)
    """

    def __init__(
        self,
        timeout: int | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        super().__init__(timeout, runner=runner)
        # Единый набор SSH-флагов из shared/ssh_opts.py (D1, U-15). BatchMode=yes добавляется
        # (единый набор) — для CI-deploy key это безопасно: ключ уже настроен, BatchMode
        # не ломает -i-аутентификацию (DevPlan 116 B5 T2).
        self.ssh_opts: list[str] = list(SSH_OPTS)

    def deliver(self, payload: Payload) -> DeliveryResult:
        # metadata — dict[str, object] (yaml/JSON-граница, W11) → str-конвертация полей
        host = str(payload.metadata.get("host", ""))
        user = str(payload.metadata.get("user", "ci-deploy"))
        key_file = str(payload.metadata.get("key_file", os.path.expanduser("~/.ssh/ci_deploy_key")))

        if not host:
            return DeliveryResult(
                success=False,
                error_message="ForcedCommandChannel requires 'host' in payload.metadata",
                exit_code=1,
            )

        remote_user = f"{user}@{host}" if user else host
        # DevPlan 116 B1 T2 (D1): verb-форма — SSH_ORIGINAL_COMMAND для forced-command диспетчера.
        # Версия из payload.version (D5): CI шлёт receive <project> <sha>.
        # T9.7 (L-8): project_name/version в SSH-команде через shlex.quote — инъекция `;`/`../`
        # в project_name не должна выполнить команду на VPS (защита ДО validate на стороне receive).
        version = payload.version or "latest"
        remote_cmd = f"receive {shlex.quote(payload.project_name)} {shlex.quote(version)}"

        # Build SSH command with piped tar
        # plan 012 T18 (F-024): пустой key_file → НЕ добавлять `-i ""` (ssh падал
        # «Warning: Identity file  not accessible» и использовал дефолтные ключи).
        ssh_cmd = [
            "ssh",
            *self.ssh_opts,
            remote_user,
            remote_cmd,
        ]
        if key_file:
            ssh_cmd[1:1] = ["-i", key_file]

        logger.info(
            "[IMP:8][ForcedCommandChannel][deliver] Delivering %s to %s via forced-command",
            payload.project_name,
            host,
        )

        start = time.monotonic()
        try:
            return self._send_forced(ssh_cmd, payload, start)
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"ForcedCommandChannel timeout after {self.timeout}s for {payload.project_name}",
                exit_code=124,
                duration_s=duration,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"ForcedCommandChannel error: {e}",
                exit_code=1,
                duration_s=duration,
            )

    # region FUNC__send_forced
    ## @purpose  Open payload tar and pipe it through stdin to the ssh remote command.
    ## @io — ⇥ ssh_cmd, payload, start → ⎋ DeliveryResult (через _receive_reply)
    ## @complexity — O(N) где N = размер файла / скорость передачи
    def _send_forced(self, ssh_cmd: list[str], payload: Payload, start: float) -> DeliveryResult:
        """Pipe payload tar through ssh stdin. Result interpreted by _receive_reply."""
        with Path(payload.tar_path).open("rb") as tar_file:
            result = self._run(
                ssh_cmd, stdin=tar_file, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        duration = time.monotonic() - start
        return self._receive_reply(result, payload, duration)

    # endregion FUNC__send_forced

    # region FUNC__receive_reply
    ## @purpose  Interpret the ssh subprocess result into a DeliveryResult.
    ## @io — ⇥ result (CompletedProcess), payload, duration → ⎋ DeliveryResult
    ## @complexity — O(1)
    # ruff: ignore[PLR6301]  # метод-интерпретатор: не использует self, но вызывается как self._receive_reply (консистентность каналов)
    def _receive_reply(
        self, result: subprocess.CompletedProcess[str], payload: Payload, duration: float
    ) -> DeliveryResult:
        """Build DeliveryResult from the ssh reply (success/failure + error_message)."""
        if result.returncode == 0:
            logger.info(
                "[IMP:9][ForcedCommandChannel][_receive_reply] Deliver SUCCESS for %s",
                payload.project_name,
            )
            return DeliveryResult(
                success=True,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=0,
                duration_s=duration,
            )

        return DeliveryResult(
            success=False,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            duration_s=duration,
            error_message=f"Forced-command failed (exit={result.returncode}): {result.stderr.strip()}",
        )

    # endregion FUNC__receive_reply


# endregion CLASS_ForcedCommandChannel
