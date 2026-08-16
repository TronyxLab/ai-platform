"""
SCPChannel — scp/rsync-based delivery channel (payload delivery via SSH agent forwarding).
"""
# GREP_SUMMARY: delivery-channels, scp, rsync, ssh, payload, deliver, mkdir, unpack, remote-cmd, timeout, shlex-quote
# STRUCTURE: ▶ deliver: validate host → _ensure_remote_dir (ssh mkdir -p) → _rsync_tar (rsync -avz) → _remote_unpack (remote-cmd.sh unpack) → ⎋ DeliveryResult
# region MODULE_CONTRACT
## @purpose  SCPChannel: deliver payload via scp/rsync + remote-cmd.sh unpack. SSH key-based
##           auth with agent forwarding. Submodule of the channels/ package (W4-B1).
## @scope    Used by DeployOrchestrator for bootstrap/rsync delivery. Host/remote_dir/user
##           passed in payload.metadata. Re-exported from channels/__init__.py.
## @invariants
##   1. Host parameter passed in payload.metadata["host"]
##   2. Remote path in payload.metadata["remote_dir"]
##   3. SSH user in payload.metadata.get("user", "root")
##   4. Uses rsync -avz for delivery, with --delete for cleanup
##   5. T9.7 (L-8): project_name/remote_dir в SSH-командах через shlex.quote (инъекция-защита)
## @rationale DevPlan 089 T2: SCP-канал для bootstrap/rsync-delivery. W4-B1: deliver (105 LOC)
##            декомпозирован на _ensure_remote_dir/_rsync_tar/_remote_unpack (поведение 1:1).
## @changes 2026-07-30 | DevPlan 089 T2 — Created (в channels.py)
## @changes 2026-08-15 | план 170 W4-B1 — вынесен в channels/scp.py, deliver декомпозирован
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from core.internal.deploy.channels.base import DeliveryChannel, DeliveryResult, Payload
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE
from core.internal.shared.ssh_opts import SSH_OPTS, build_rsync_ssh_opts
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT, SSH_READ_TIMEOUT

logger = logging.getLogger(__name__)


# region CLASS_SCPChannel


class SCPChannel(DeliveryChannel):
    """SCP/rsync-based delivery channel. Uses SSH agent forwarding.

    ## @purpose — Deliver payload via scp/rsync + remote-cmd.sh unpack.
    ##            Uses SSH key-based auth with agent forwarding.
    ## @io — ⇥ Payload → ⎋ DeliveryResult
    ## @complexity — O(N) where N = file size / transfer speed
    ## @invariants
    ##   - Host parameter passed in payload.metadata["host"]
    ##   - Remote path in payload.metadata["remote_dir"]
    ##   - SSH user in payload.metadata.get("user", "root")
    ##   - Uses rsync -avz for delivery, with --delete for cleanup
    """

    def __init__(
        self,
        timeout: int | None = None,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ):
        super().__init__(timeout, runner=runner)
        # Единый набор SSH-флагов из shared/ssh_opts.py (D1, U-15 — 5 копий заменены импортом).
        # list() — копия: защита от случайной мутации общего канона.
        self.ssh_opts: list[str] = list(SSH_OPTS)

    def deliver(self, payload: Payload) -> DeliveryResult:
        # metadata — dict[str, object] (yaml/JSON-граница, W11) → str-конвертация полей
        host = str(payload.metadata.get("host", ""))
        remote_dir = str(payload.metadata.get("remote_dir", DEFAULT_PROJECTS_BASE))
        user = str(payload.metadata.get("user", "root"))

        if not host:
            return DeliveryResult(
                success=False,
                error_message="SCPChannel requires 'host' in payload.metadata",
                exit_code=1,
            )

        remote = f"{user}@{host}" if user else host
        target = f"{remote}:{remote_dir}/{payload.project_name}/"

        start = time.monotonic()
        try:
            mkdir_error = self._ensure_remote_dir(remote, remote_dir, payload.project_name, start)
            if mkdir_error is not None:
                return mkdir_error
            rsync_result = self._rsync_tar(target, payload.tar_path, start)
            if not rsync_result.success:
                return rsync_result
            self._remote_unpack(remote, remote_dir, payload.project_name)
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"SCPChannel timeout after {self.timeout}s for {payload.project_name}",
                exit_code=124,
                duration_s=duration,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            duration = time.monotonic() - start
            return DeliveryResult(
                success=False,
                error_message=f"SCPChannel error: {e}",
                exit_code=1,
                duration_s=duration,
            )

        logger.info(
            "[IMP:9][SCPChannel][deliver] Deliver SUCCESS for %s to %s",
            payload.project_name,
            host,
        )
        return rsync_result

    # region FUNC__ensure_remote_dir
    ## @purpose  Ensure remote dir exists before rsync (T9.7 L-8: project_name через
    ##            shlex.quote — инъекция `;`/`&&` не должна выполнить команду на хосте).
    ## @io — ⇥ remote, remote_dir, project_name, start → ⎋ DeliveryResult | None (None = ok)
    ## @complexity — O(1) — одна ssh-команда
    def _ensure_remote_dir(
        self, remote: str, remote_dir: str, project_name: str, start: float
    ) -> DeliveryResult | None:
        """Create remote dir via ssh mkdir -p. Returns DeliveryResult on failure, None on success."""
        mkdir_remote_cmd = f"mkdir -p {shlex.quote(remote_dir)}/{shlex.quote(project_name)}"
        ssh_cmd = ["ssh", *self.ssh_opts, remote, mkdir_remote_cmd]
        logger.info(
            "[IMP:8][SCPChannel][_ensure_remote_dir] Creating remote dir %s/%s on %s",
            remote_dir,
            project_name,
            remote,
        )
        mkdir_result = self._run(ssh_cmd, capture_output=True, text=True, timeout=SSH_CONNECT_TIMEOUT, check=False)
        if mkdir_result.returncode != 0:
            return DeliveryResult(
                success=False,
                stdout=mkdir_result.stdout,
                stderr=mkdir_result.stderr,
                exit_code=mkdir_result.returncode,
                duration_s=time.monotonic() - start,
                error_message=f"mkdir failed on {remote}: {mkdir_result.stderr.strip()}",
            )
        return None

    # endregion FUNC__ensure_remote_dir

    # region FUNC__rsync_tar
    ## @purpose  Rsync tar file to remote target dir.
    ## @io — ⇥ target, tar_path, start → ⎋ DeliveryResult (success=True → stdout из rsync)
    ## @complexity — O(N) где N = размер файла / скорость передачи
    def _rsync_tar(self, target: str, tar_path: Path, start: float) -> DeliveryResult:
        """Rsync payload tar to target. Returns DeliveryResult with rsync stdout on success."""
        rsync_cmd = [
            "rsync",
            "-avz",
            "--progress",
            "-e",
            build_rsync_ssh_opts(),
            str(tar_path),
            target,
        ]
        logger.info("[IMP:8][SCPChannel][_rsync_tar] Rsyncing %s → %s", tar_path, target)
        rsync_result = self._run(rsync_cmd, capture_output=True, text=True, timeout=self.timeout, check=False)
        duration = time.monotonic() - start

        if rsync_result.returncode == 0:
            return DeliveryResult(
                success=True,
                stdout=rsync_result.stdout,
                exit_code=0,
                duration_s=duration,
            )

        return DeliveryResult(
            success=False,
            stdout=rsync_result.stdout,
            stderr=rsync_result.stderr,
            exit_code=rsync_result.returncode,
            duration_s=duration,
            error_message=f"rsync failed: {rsync_result.stderr.strip()}",
        )

    # endregion FUNC__rsync_tar

    # region FUNC__remote_unpack
    ## @purpose  Run remote-cmd.sh unpack if available (T9.7: shlex.quote(project_name)).
    ##            Результат unpack не проверяется — unpack-скрипт опционален (if available).
    ## @io — ⇥ remote, remote_dir, project_name → ⎋ None
    ## @complexity — O(1) — одна ssh-команда
    def _remote_unpack(self, remote: str, remote_dir: str, project_name: str) -> None:
        """Invoke remote-cmd.sh unpack via ssh. Result intentionally ignored."""
        # M12 (security hardening): путь remote-cmd.sh экранируется через shlex.quote
        # (project_name/remote_dir — защита от инъекции `;`/пробела в remote-shell;
        # ранее путь был сырым f-string, квотировался только аргумент unpack).
        unpack_script = (
            f"{shlex.quote(remote_dir)}/{shlex.quote(project_name)}/remote-cmd.sh unpack {shlex.quote(project_name)}"
        )
        self._run(
            ["ssh", *self.ssh_opts, remote, unpack_script],
            capture_output=True,
            timeout=SSH_READ_TIMEOUT,
            check=False,
        )

    # endregion FUNC__remote_unpack


# endregion CLASS_SCPChannel
