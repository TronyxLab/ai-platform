#!/usr/bin/env python3
# GREP_SUMMARY: payload-deliverer, tar-gz, stdin, validate, extract, atomic-move, whitelist, compose, deliver
# STRUCTURE: ▶ CLI(argparse:deliver) → PayloadDeliverer.deliver(project,org,stdin) → _read_payload(max_size=1MiB) →
#            _validate_and_extract(tar_bytes,tmp_dir) => check path_traversal|symlinks|hardlinks|whitelist|compose_file →
#            _atomic_move(extracted_files,target_dir) → ⎋ DeliverResult(success|files_delivered)
# region MODULE_CONTRACT
## @purpose  Validate and atomically extract tar.gz payload delivered via stdin.
##           Used by the receive-канал (DevPlan 116 B1 T2/T7): payload ассемблируется на
##           операторской стороне (deliver) и доставляется через ForcedCommandChannel
##           (remote_cmd "receive <project> <version>") — VPS-side dispatch → DeployOrchestrator.receive().
##           Migrated from handle_deliver() in the deploy shell (Wave 5e Strangler-Fig).
## @scope    Pure file I/O + tar validation. Zero Docker dependency. Can be reused by
##           other entrypoints (reconcile-projects.sh, context_deployer.py, etc.).
## @invariants
##   1. stdin read with 1 MiB hard cap — payload >1MiB rejected
##   2. Path traversal defense: no subdirectory files allowed
##   3. No symlinks, hardlinks, or non-regular files in archive
##   4. Whitelist-only: docker-compose.yml, compose.yaml, ai-platform.yaml, .env.platform
##   5. Must include at least one docker-compose.yml or compose.yaml
##   6. Atomic move to PROJECTS_BASE/<org>/<project> — cleanup on failure
##   7. No secrets or tokens in output — all audit to stderr
## @rationale
##   🧐 TRAP[DECISION] · 2026-07-17 · — · Deliver via stdin tar.gz, not sftp/git-pull
##   · Rejected: sftp-chroot user (second SSH key), git-pull projects (deploy-keys on node)
##   · Reason: zero new channels/keys, restrict preserved, decision confirmed by user
##   · Rev: if payload size exceeds 1M regularly → consider SCP variant
##
##   ⚠️ TRAP[BUG] · 2026-07-20 · receive exit 1 despite success (актуализирован B1 T7)
##   · Symptom: first-time deliver → .deploy-snapshots/ not found → ERR trap → exit 1
##   · Root: _write_deploy_result() → cat > .../deploy-result.json fails if dir missing
##   · Fix: mkdir -p before writing (idempotent) — handled by shell facade trap/EXIT
## @changes 2026-07-26 · DevPlan 036E — Created (Wave 5e Strangler-Fig migration from handle_deliver)
##           2026-08-01 · DevPlan 116 B1 T7 — docstring: deliver-verb → receive-канал
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import io
import logging
import os
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from core.internal.deploy.channels import Payload
from core.internal.shared.compose_files import PROJECT_COMPOSE_FILENAMES, PROJECT_PAYLOAD_FILENAMES

# B2: канонический дефолт PROJECTS_BASE — shared/deploy_paths (литерал /opt/projects удалён)
from core.internal.shared.deploy_paths import DEFAULT_PROJECTS_BASE
from core.internal.shared.project_registry import validate_project_name

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

MAX_PAYLOAD_SIZE = 1 * 1024 * 1024  # 1 MiB

# REF-0105 (triple-sync): WHITELIST_FILES и _PAYLOAD_FILE_NAMES — проекции ЕДИНОЙ константы
# shared/compose_files.PROJECT_PAYLOAD_FILENAMES (compose-подмножество + ai-platform.yaml +
# .env.platform + practices.lock). CI-сторона (deploy-project.yml FILES) синхронизируется
# structural-тестом tests/unit/test_payload_whitelist_triple_sync.py.
# ⚠️ TRAP[BUG] · 2026-08-06 · HI · B20a (141 r2): practices.lock не доставлялся payload'ом
# · Symptom: после deploy-project на ноде /opt/projects/<p>/practices.lock ОТСУТСТВОВАЛ →
# ·   K3 verify state=unmanaged вечно. Противоречит AGENTS.md §Наследование практик (DevPlan 137):
# ·   «practices.lock ... доставляется на VPS payload'ом receive».
# · Fix: practices.lock добавлен в whitelist (ныне — в канон PROJECT_PAYLOAD_FILENAMES).
# · Rev: при расширении payload новыми GENERATED-файлами — правка ТОЛЬКО канона + CI-шага.
WHITELIST_FILES: frozenset[str] = frozenset(PROJECT_PAYLOAD_FILENAMES)

# Порядок файлов payload'а для assemble_payload: канонический file-list (REF-0105)
_PAYLOAD_FILE_NAMES: tuple[str, ...] = PROJECT_PAYLOAD_FILENAMES


# ── Custom exceptions ───────────────────────────────────────────────────────


class SizeLimitError(Exception):
    """Raised when payload exceeds size limit."""


class PayloadValidationError(Exception):
    """Raised when payload fails content validation.

    # 170 W2-A2 (B3): переименован из ValidationError — устранение коллизии имён
    # с core/internal/deploy/preflight.py:104 (FQDN-валидация, чужой класс). Коллизия
    # двух одноимённых ValidationError в одном пакете deploy/ маскировала происхождение
    # ошибки в except-обработчиках. Потребители класса — только этот модуль (deliver()).
    """


# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class DeliverResult:
    """Result of a payload delivery operation."""

    success: bool
    project: str
    org: str | None = None
    files_delivered: int = 0
    error_message: str | None = None


# ── PayloadDeliverer ────────────────────────────────────────────────────────

# region CLASS_PayloadDeliverer


class PayloadDeliverer:
    """Validate and atomically extract tar.gz payload delivered via stdin.

    ## @rationale DevPlan 089 T8: assemble_payload() is the public API for DeployOrchestrator.
    ##            deliver() preserved for backward compatibility with the shell facade.

    Zero Docker dependency. Pure file I/O + tar validation.
    """

    def __init__(self, projects_base: str = DEFAULT_PROJECTS_BASE):
        self.projects_base = projects_base

    # region FUNC_assemble_payload
    ## @purpose  Assemble project files into a Payload dataclass for DeployOrchestrator.
    ##           Creates tar.gz of project files (docker-compose.yml, ai-platform.yaml, .env.platform)
    ##           from the project directory. Returns Payload with tar_path pointing to created archive.
    ## @io       ⇥ project_name: str, version: str, project_dir: str, metadata: dict → ⎋ Payload
    ## @complexity — O(N) where N = files to include
    ## @invariants
    ##   - Creates tar.gz in temp directory (caller responsible for cleanup)
    ##   - Only includes whitelisted files (same as deliver)
    ##   - Returns Payload with project_name and version
    def assemble_payload(
        self,
        project_name: str,
        version: str = "",
        project_dir: str = "",
        metadata: dict[str, object] | None = None,
    ) -> Payload:
        """Assemble project files into a deploy payload.

        Args:
            project_name: Project name.
            version: Version/tag.
            project_dir: Project directory path.
            metadata: Additional metadata.

        Returns:
            Payload with tar_path pointing to created tar.gz.
        """
        import tarfile
        import tempfile
        from pathlib import Path

        base_dir = project_dir or Path(self.projects_base) / project_name
        metadata = metadata or {}

        # Create tar.gz
        tar_fd, tar_path = tempfile.mkstemp(suffix=".tar.gz", prefix=f"payload-{project_name}-")
        os.close(tar_fd)

        with tarfile.open(tar_path, "w:gz") as tar:
            for fname in _PAYLOAD_FILE_NAMES:
                fpath = Path(base_dir) / fname
                if Path(fpath).is_file():
                    tar.add(fpath, arcname=fname)

        logger.info(
            "[IMP:9][assemble_payload] Assembled payload for %s (version=%s): %s",
            project_name,
            version,
            tar_path,
        )
        from core.internal.deploy.channels import Payload

        return Payload(
            tar_path=Path(tar_path),
            project_name=project_name,
            version=version,
            metadata=metadata,
        )

    # endregion FUNC_assemble_payload

    # region FUNC_deliver
    ## @purpose  Full deliver flow: read stdin tar.gz → validate → atomically extract to target.
    ## @io       ⇥ project, org, projects_base, stdin → ⎋ DeliverResult
    ## @complexity — O(N) where N = number of tar entries
    ## @invariants
    ##   - stdin max 1 MiB (excess → fail, nothing written)
    ##   - Whitelist-only files allowed
    ##   - No path traversal, no symlinks
    ##   - Atomic move to target dir — partial failure = cleanup
    def deliver(
        self,
        project: str,
        org: str | None = None,
        projects_base: str | None = None,
        stdin: BinaryIO = sys.stdin.buffer,
    ) -> DeliverResult:
        """Read, validate, and atomically extract tar.gz payload.

        Args:
            project: Project name.
            org: Optional org/context name.
            projects_base: Base projects directory. Defaults to self.projects_base.
            stdin: Input stream (overridable for testing).

        Returns:
            DeliverResult with success status.
        """
        base = projects_base or self.projects_base
        target_dir = Path(base) / f"{org + '/' if org else ''}{project}"

        logger.info(
            "[IMP:9][deliver][start] Deliver START: project=%s org=%s target=%s", project, org or "", target_dir
        )

        # Validate project name
        if not validate_project_name(project):
            msg = f"Invalid project name: {project}"
            logger.error("[IMP:10][deliver][validation] %s", msg)
            return DeliverResult(success=False, project=project, org=org, error_message=msg)

        # ── Read payload ──
        try:
            tar_bytes = self._read_payload(stdin)
        except SizeLimitError as e:
            logger.error("[IMP:10][deliver][size] %s", e)
            return DeliverResult(success=False, project=project, org=org, error_message=str(e))

        if not tar_bytes:
            msg = "Empty payload received"
            logger.error("[IMP:10][deliver][empty] %s", msg)
            return DeliverResult(success=False, project=project, org=org, error_message=msg)

        # ── Validate and extract to temp ──
        tmp_dir = tempfile.mkdtemp(prefix="deliver-")
        # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
        try:
            extracted = self._validate_and_extract(tar_bytes, tmp_dir)
            if not extracted:
                msg = "No valid files found in payload"
                logger.error("[IMP:10][deliver][extract] %s", msg)
                return DeliverResult(success=False, project=project, org=org, error_message=msg)

            # ── Atomic move ──
            self._atomic_move(extracted, str(target_dir))
            logger.info("[IMP:9][deliver][done] Deliver DONE: %d files to %s", len(extracted), target_dir)
            return DeliverResult(success=True, project=project, org=org, files_delivered=len(extracted))

        except PayloadValidationError as e:
            logger.error("[IMP:10][deliver][validation] %s", e)
            return DeliverResult(success=False, project=project, org=org, error_message=str(e))
        finally:
            # Cleanup temp dir
            if Path(tmp_dir).is_dir():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # endregion FUNC_deliver

    # region FUNC__read_payload
    ## @purpose  Read stdin with 1 MiB hard cap.
    ## @io       ⇥ stdin, max_size → ⎋ bytes
    ## @complexity — O(N) where N = bytes read (cap at max_size+1)
    ## @invariants
    ##   - Reads max_size + 1 bytes to detect oversize
    ##   - Raises SizeLimitError if payload exceeds max_size
    @staticmethod
    def _read_payload(stdin: BinaryIO, max_size: int = MAX_PAYLOAD_SIZE) -> bytes:
        """Read stdin with size cap.

        Args:
            stdin: Input binary stream.
            max_size: Maximum allowed payload size in bytes.

        Returns:
            Payload bytes.

        Raises:
            SizeLimitError: If payload exceeds max_size.
        """
        data = stdin.read(max_size + 1)  # Read 1 extra byte for oversize detection
        if len(data) > max_size:
            msg = f"Payload exceeds {max_size} byte limit ({len(data)} bytes read)"
            raise SizeLimitError(msg)
        return data

    # endregion FUNC__read_payload

    # region FUNC__validate_and_extract
    ## @purpose  Validate tar.gz content and extract valid files to temp dir.
    ## @io       ⇥ tar_bytes, tmp_dir → ⎋ list[Path] (extracted valid files)
    ## @complexity — O(N) where N = number of tar entries
    ## @invariants
    ##   - Rejects subdirectory files (path traversal defense)
    ##   - Rejects symlinks, hardlinks, non-regular files
    ##   - Validates against WHITELIST_FILES set
    ##   - Requires at least one docker-compose.yml or compose.yaml
    def _validate_and_extract(self, tar_bytes: bytes, tmp_dir: str) -> list[Path]:
        """Validate and extract tar.gz content.

        Args:
            tar_bytes: Raw tar.gz data.
            tmp_dir: Temporary directory for extraction.

        Returns:
            List of extracted file paths.

        Raises:
            PayloadValidationError: If content validation fails.
        """
        extracted: list[Path] = []
        found_compose = False

        buf = io.BytesIO(tar_bytes)
        # ruff: ignore[PLW0717] — тело try присваивает имена, читаемые except/после — извлечение ломает видимость
        try:
            with tarfile.open(fileobj=buf, mode="r:gz") as tar:
                # First pass: validate all entries
                for member in tar.getmembers():
                    self._validate_entry(member)

                # Second pass: extract valid entries
                for member in tar.getmembers():
                    tar.extract(member, path=tmp_dir, filter="data")
                    extracted.append(Path(tmp_dir) / member.name)
                    if member.name in PROJECT_COMPOSE_FILENAMES:
                        found_compose = True

        except (tarfile.TarError, OSError) as e:
            msg = f"Tar extraction failed: {e}"
            raise PayloadValidationError(msg) from e

        if not found_compose:
            msg = "Missing docker-compose.yml or compose.yaml in payload"
            raise PayloadValidationError(msg)

        # Verify extracted files exist and are regular
        result: list[Path] = [p for p in extracted if p.is_file() and not p.is_symlink()]

        return result

    # endregion FUNC__validate_and_extract

    # region FUNC__validate_entry
    ## @purpose  Validate a single tar entry against security rules.
    ## @io       ⇥ member: tarfile.TarInfo → ⎋ None (raises on fail)
    ## @complexity — O(1)
    ## @invariants
    ##   - Rejects subdirectory entries (path traversal)
    ##   - Rejects symlinks, hardlinks, non-regular files
    ##   - Rejects non-whitelisted filenames
    @staticmethod
    def _validate_entry(member: tarfile.TarInfo) -> None:
        """Validate a single tar entry.

        Args:
            member: TarInfo entry to validate.

        Raises:
            PayloadValidationError: If entry violates any security rule.
        """
        name = member.name

        # No subdirectories (path traversal defense)
        if "/" in name.rstrip("/"):
            msg = f"Subdirectory entry rejected (path traversal): {name}"
            raise PayloadValidationError(msg)

        # No symlinks
        if member.issym() or member.islnk():
            msg = f"Symlink/link rejected: {name}"
            raise PayloadValidationError(msg)

        # Regular files only
        if not member.isfile():
            msg = f"Non-regular file rejected: {name}"
            raise PayloadValidationError(msg)

        # Whitelist check
        if name not in WHITELIST_FILES:
            msg = f"Non-whitelisted file: {name} (allowed: {', '.join(sorted(WHITELIST_FILES))})"
            raise PayloadValidationError(msg)

    # endregion FUNC__validate_entry

    # region FUNC__atomic_move
    ## @purpose  Atomically move extracted files to target directory.
    ## @io       ⇥ files, target_dir → ⎋ None
    ## @complexity — O(N) where N = number of files
    ## @invariants
    ##   - Creates target dir if not exists
    ##   - Uses shutil.move for cross-filesystem safety
    ##   - No partial state on failure (caller cleans up)
    @staticmethod
    def _atomic_move(files: list[Path], target_dir: str) -> None:
        """Move extracted files to target directory.

        Args:
            files: List of extracted file paths.
            target_dir: Target directory path.

        Raises:
            OSError: On move failure.
        """
        Path(target_dir).mkdir(exist_ok=True, parents=True)
        for f in files:
            dest = Path(target_dir) / f.name
            shutil.move(str(f), dest)
            logger.info("[IMP:8][atomic-move] Moved %s → %s", f.name, dest)

    # endregion FUNC__atomic_move


# endregion CLASS_PayloadDeliverer


# ── CLI ──────────────────────────────────────────────────────────────────────


# region CLI
## @purpose  CLI entrypoint with argparse for deliver subcommand (argv-канон, W4a).
## @io       ⇥ argv: list[str] | None (None = sys.argv[1:]) → ⎋ exit 0|1
def main(argv: list[str] | None = None) -> int:
    """CLI deliver subcommand — main() -> int (sys.exit только в __main__, контракт core/)."""
    parser = argparse.ArgumentParser(description="Payload Deliverer — validate and extract tar.gz payload via stdin")
    sub = parser.add_subparsers(dest="command", required=True)

    deliver_parser = sub.add_parser("deliver", help="Deliver payload via stdin")
    deliver_parser.add_argument("project", help="Project name")
    deliver_parser.add_argument("org", nargs="?", default=None, help="Optional org/context name")
    deliver_parser.add_argument("--projects-base", default=DEFAULT_PROJECTS_BASE, help="Projects base directory")

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    from dataclasses import dataclass
    from typing import cast

    @dataclass
    class _CliArgs:
        command: str
        project: str
        org: str
        projects_base: str

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    if args.command == "deliver":
        deliverer = PayloadDeliverer()
        result = deliverer.deliver(
            project=args.project,
            org=args.org,
            projects_base=args.projects_base,
        )
        print(f"Deliver {'SUCCESS' if result.success else 'FAILED'}: {result.files_delivered} files")
        return 0 if result.success else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
# endregion CLI
