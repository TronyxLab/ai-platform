#!/usr/bin/env python3
# GREP_SUMMARY: provision, platform-env, docker-network, volume-dir, ci-env, idempotent, dry-run
# STRUCTURE: ▶ cli:args→dispatch → ◇ load_platform_env→PlatformEnv → ⊕ provision_networks(subprocess docker) → ⊕ provision_volumes(mkdir -p) → ⊕ provision_env(GITHUB_ENV|stderr) → ⊕ provision_profiles → ⎋ exit 0|1|10
# region MODULE_CONTRACT
## @purpose  Python provisioner — replaces 13 inline python3 calls in provision-environment.sh.
##           Reads platform-env.yaml, creates Docker networks, volume directories, CI env vars.
## @scope    Called from provision-environment.sh (shell wrapper) with --scope arg.
##           Single scope per invocation. Audit-диспетч — bootstrap/provision_env.py (164 W3.5-1).
## @invariants
##   - LDD block name: [provision] (NOT [provisioner]) — backward compat with 30 test assertions
##   - Networks: docker inspect → exists → skip, else docker network create
##   - Volumes: os.path.isdir → exists → skip, else os.makedirs(exist_ok=True)
##   - Env: GITHUB_ENV from os.environ → write KEY=VALUE; None → print to stderr
##   - Exit codes: 0=success, 1=parse error, 10=docker unavailable (PlatformFatalError, D4)
## @rationale  Eliminates YAML→JSON→shell→python3 round-trips, single YAML parse per scope.
##             All business logic testable natively (no subprocess for JSON parsing).
## @changes  2026-08-16 | DevPlan 177 W3.5 — load_platform_env + типы PlatformEnv/NetworkConfig/
##                      VolumeConfig перенесены в shared/yaml_loader.py (re-export для обратной
##                      совместимости: тесты и provisioner.main импортируют из provisioner)
# ⚠️ TRAP[DECISION] · 2026-08-15 · — · provisioner.py + bootstrap/provision_env.py — НЕ дубль (172 W5.2)
# · Rejected: слияние в один модуль (аудит W5.2)
# · Reason: provisioner.py = бизнес-логика провижининга (networks/volumes/env/profiles),
# ·   bootstrap/provision_env.py = CLI-оркестратор (parse_args, 'all'-расширение, dedup,
# ·   per-scope dispatch + audit). Слои каноничны: provision-environment.sh (27 LOC фасад)
# ·   → provision_env.py → provisioner.main (DI-параметр provisioner_main). Канон 164 W3.5-1.
# · Rev: если provision_env.py начнёт дублировать бизнес-логику (не только dispatch) — слить.
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import yaml

from core.internal.shared import docker_ops  # W1: docker network inspect/create примитивы (гейт docker_sole_path)
from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# W3.5 (DevPlan 177): типизированный SoT-YAML читатель + типы — единый shared/yaml_loader.py.
# Re-export для обратной совместимости: тесты (test_provisioner_volumes_owner) и main()
# импортируют PlatformEnv/VolumeConfig/load_platform_env из provisioner.
from core.internal.shared.yaml_loader import (
    NetworkConfig,  # ruff: ignore[F401] — re-export (pub API provisioner, исторический импорт-путь)
    PlatformEnv,
    VolumeConfig,  # ruff: ignore[F401] — re-export (test_provisioner_volumes_owner контракт)
    load_platform_env,
)

# ── Logger setup ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_ch = logging.StreamHandler(sys.stderr)
_ch.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [_ch]


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ProvisionResult:
    """Result of a single scope provision operation."""

    scope: str
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Scope: Networks ───────────────────────────────────────────────────────────


def provision_networks(
    platform_env: PlatformEnv,
    dry_run: bool = False,
    *,
    network_inspect_fn: Callable[[str], bool] | None = None,
    network_create_fn: Callable[[str, str], bool] | None = None,
    which_fn: Callable[[str], str | None] | None = None,
) -> ProvisionResult:
    """Create Docker networks from platform-env.networks.

    IDEMPOTENT: docker network inspect → exists → skip, else docker network create.
    DI (W-H DevPlan 163): network_inspect_fn/network_create_fn — None = docker_ops каноны;
    тесты передают fake-каналы (0 патчей subprocess/docker_ops).
    which_fn (DevPlan 167 D3): fake shutil.which для docker-доступности; None → канон.
    🧐 TRAP[DI-SEAM] · 2026-08-14 · — · which_fn на provision_networks (docker-detect seam)
    · Rejected: прямой вызов shutil.which("docker")
    · Reason: seam = тестируемость реального docker-unavailable Fatal(10) без глобального
    ·   патча shutil.which; поведение по умолчанию (None → канон) неизменно
    · Rev: появление второго which-потребителя в provisioner → общий facts-объект
    Uses subprocess.run for docker commands.
    """
    result = ProvisionResult(scope="networks")

    logger.info("[IMP:7][provision][networks] Reading platform-env.yaml networks")

    if not platform_env.networks:
        logger.info("[IMP:8][provision][networks] No networks defined — nothing to do")
        return result

    which_bin = shutil.which if which_fn is None else which_fn
    if not dry_run and not which_bin("docker"):
        logger.error("[IMP:10][provision][networks] FATAL: Docker is not available")
        # D4 (DevPlan 116 B4): docker unavailable — невосстановимо без ручного действия → PlatformFatalError (10).
        # Ранее sys.exit(2); shell-фасады обновлены (provision.mk/CI) — exit-код docker-unavailable теперь 10.
        msg = "Docker is not available — provision networks requires docker"
        raise PlatformFatalError(msg)

    for net in platform_env.networks:
        if dry_run:
            logger.info(
                "[IMP:7][provision][networks] DRY-RUN: Would create network: %s (driver: %s)",
                net.name,
                net.driver,
            )
            result.created += 1
            continue

        inspect_impl = docker_ops.docker_network_inspect if network_inspect_fn is None else network_inspect_fn
        create_impl = docker_ops.docker_network_create if network_create_fn is None else network_create_fn
        # Check if network exists (W1: docker network inspect — shared/docker_ops)
        if inspect_impl(net.name):
            logger.info("[IMP:7][provision][networks] SKIP: network %s already exists", net.name)
            result.skipped += 1
        else:
            logger.info("[IMP:7][provision][networks] Creating network: %s (driver: %s)", net.name, net.driver)
            if not create_impl(net.name, net.driver):
                msg = f"Failed to create network {net.name}"
                logger.error("[IMP:10][provision][networks] %s", msg)
                result.errors.append(msg)
            else:
                result.created += 1

    logger.info(
        "[IMP:9][provision][networks] Networks provisioned: %d created, %d skipped",
        result.created,
        result.skipped,
    )
    return result


# ── Scope: Volumes ────────────────────────────────────────────────────────────


def provision_volumes(
    platform_env: PlatformEnv,
    dry_run: bool = False,
    *,
    isdir_fn: Callable[[str], bool] | None = None,
    makedirs_fn: Callable[..., object] | None = None,
) -> ProvisionResult:
    """Create volume directories from platform-env.volumes.

    IDEMPOTENT: os.path.isdir → exists → skip, else mkdir -p.
    DI (W-H DevPlan 163): isdir_fn/makedirs_fn — None = os.path.isdir/os.makedirs (канон).
    On permission error: log warning, add to skipped count (non-fatal).
    Owner: если VolumeConfig.owner задан ("uid:gid") — chown применяется и при
    создании, и при несовпадении владельца существующей директории (TRAP[BUG]
    2026-08-03: wal-archive root:root → postgres archive_command Permission denied).
    """
    result = ProvisionResult(scope="volumes")

    logger.info("[IMP:7][provision][volumes] Reading platform-env.yaml volumes")

    if not platform_env.volumes:
        logger.info("[IMP:8][provision][volumes] No volumes defined — nothing to do")
        return result

    for vol in platform_env.volumes:
        vol_path = vol.path

        if dry_run:
            logger.info(
                "[IMP:7][provision][volumes] DRY-RUN: Would create directory: %s",
                vol_path,
            )
            result.created += 1
            continue

        isdir_impl = os.path.isdir if isdir_fn is None else isdir_fn
        if isdir_impl(vol_path):
            logger.info("[IMP:7][provision][volumes] SKIP: directory already exists: %s", vol_path)
            result.skipped += 1
            if vol.owner and not _owner_matches(vol_path, vol.owner):
                _chown_dir(vol_path, vol.owner, result)
        else:
            logger.info("[IMP:7][provision][volumes] Creating directory: %s", vol_path)
            try:
                (os.makedirs if makedirs_fn is None else makedirs_fn)(vol_path, exist_ok=True)
                result.created += 1
                if vol.owner:
                    _chown_dir(vol_path, vol.owner, result)
            except PermissionError:
                logger.warning(
                    "[IMP:7][provision][volumes] WARN: Cannot create %s (permission denied)",
                    vol_path,
                )
                result.skipped += 1

    logger.info(
        "[IMP:9][provision][volumes] Volumes provisioned: %d created, %d skipped",
        result.created,
        result.skipped,
    )
    return result


# ── Owner helpers (TRAP[BUG] 2026-08-03: wal-archive postgres-owner) ──────────
def _owner_matches(path: str, owner: str) -> bool:
    """Совпадает ли владелец директории с owner="uid:gid" (числовым или именным)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    user, _, group = owner.partition(":")
    uid = int(user) if user.isdigit() else None
    gid = int(group) if group.isdigit() else None
    return (uid is None or st.st_uid == uid) and (gid is None or st.st_gid == gid)


def _chown_dir(path: str, owner: str, result: ProvisionResult) -> None:
    """chown директории (uid:gid); ошибка → warning + errors (non-fatal)."""
    user, _, group = owner.partition(":")
    uid = int(user) if user.isdigit() else user
    gid = int(group) if group.isdigit() else group
    try:
        shutil.chown(path, user=uid, group=gid)
        logger.info("[IMP:9][provision][volumes] chown %s → %s", path, owner)
    except (PermissionError, OSError) as exc:
        logger.warning("[IMP:7][provision][volumes] WARN: chown %s failed: %s", path, exc)
        result.errors.append(f"chown {path}: {exc}")


# ── Scope: Env ────────────────────────────────────────────────────────────────


def provision_env(
    platform_env: PlatformEnv,
    dry_run: bool = False,
    github_env: str | None = None,
) -> ProvisionResult:
    """Export CI environment variables.

    - If github_env is set (GITHUB_ENV file path): write KEY=VALUE lines to file
    - If github_env is None and not dry_run: print to stderr (local mode)
    - If dry_run: print "DRY-RUN: Would export KEY=VALUE" to stdout
    """
    result = ProvisionResult(scope="env")

    logger.info("[IMP:7][provision][env] Reading platform-env.yaml env_defaults")

    if not platform_env.env_defaults:
        logger.info("[IMP:8][provision][env] No env_defaults defined — nothing to do")
        return result

    if dry_run:
        for k, v in platform_env.env_defaults.items():
            print(f"DRY-RUN: Would export {k}={v}")
        count = len(platform_env.env_defaults)
        result.created = count
        logger.info("[IMP:7][provision][env] DRY-RUN: Would export %d env vars", count)
        return result

    if github_env:
        logger.info("[IMP:7][provision][env] Exporting env vars to GITHUB_ENV=%s", github_env)
        with Path(github_env).open("a", encoding="utf-8") as f:
            for k, v in platform_env.env_defaults.items():
                f.write(f"{k}={v}\n")
        count = len(platform_env.env_defaults)
        logger.info("[IMP:9][provision][env] %d env vars exported to GITHUB_ENV", count)
    else:
        logger.info("[IMP:7][provision][env] GITHUB_ENV not set — printing env vars to stderr")
        for k, v in platform_env.env_defaults.items():
            print(f"  {k}={v}", file=sys.stderr)
        logger.info("[IMP:9][provision][env] Env vars printed (GITHUB_ENV not set — local mode)")

    result.created = len(platform_env.env_defaults)
    return result


# ── Scope: Profiles ───────────────────────────────────────────────────────────


def provision_profiles(
    platform_env: PlatformEnv,
) -> ProvisionResult:
    """Report available profiles count. Logs profile names at IMP:8."""
    result = ProvisionResult(scope="profiles")

    count = len(platform_env.profiles)
    logger.info("[IMP:8][provision][profiles] Profiles available: %d", count)
    result.created = count
    return result


# ── CLI ───────────────────────────────────────────────────────────────────────


def main(
    argv: list[str] | None = None,
    *,
    network_inspect_fn: Callable[[str], bool] | None = None,
    network_create_fn: Callable[[str, str], bool] | None = None,
    isdir_fn: Callable[[str], bool] | None = None,
    makedirs_fn: Callable[..., object] | None = None,
) -> int:
    """CLI entry point: python3 provisioner.py --scope <scope> --platform-env <path> [--dry-run]

    Exit codes:
        0 — success (all resources created or already exist)
        1 — parse error (YAML invalid, file not found, unknown scope)
        10 — docker unavailable (for --scope networks) — PlatformFatalError (D4, DevPlan 116 B4)

    DI (W-H DevPlan 163): argv=None → sys.argv[1:]; network/fs-каналы пробрасываются
    в provision_networks/volumes (0 патчей subprocess/os в тестах main).
    """
    parser = argparse.ArgumentParser(
        description="Idempotent environment provisioner — reads platform-env.yaml",
    )

    class _Args(argparse.Namespace):
        """Typed argparse namespace (W11: Namespace attribute access is Any).

        ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
        """

        scope: ClassVar[str]
        platform_env: ClassVar[Path]
        dry_run: ClassVar[bool]

    parser.add_argument(
        "--scope",
        required=True,
        choices=["networks", "volumes", "env", "profiles"],
        help="Scope of provisioning (required)",
    )
    parser.add_argument(
        "--platform-env",
        required=True,
        type=Path,
        help="Path to platform-env.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print actions without executing",
    )

    args = parser.parse_args(None if argv is None else argv, namespace=_Args())

    yaml_path: Path = args.platform_env

    # Validate file exists
    if not yaml_path.is_file():
        logger.error(
            "[IMP:10][provision] FATAL: platform-env.yaml not found at %s",
            yaml_path,
        )
        return 1

    # Load platform env
    try:
        platform_env = load_platform_env(yaml_path)
    except yaml.YAMLError as e:
        logger.error("[IMP:10][provision] FATAL: Cannot parse %s: %s", yaml_path, e)
        return 1
    except (FileNotFoundError, OSError) as e:
        logger.error("[IMP:10][provision] FATAL: Error reading %s: %s", yaml_path, e)
        return 1

    # Dispatch
    scope: str = args.scope
    dry_run: bool = args.dry_run

    # ruff: ignore[PLW0717] — нужно >5 свободных локальных переменных — извлечение неразумно
    try:
        if scope == "networks":
            provision_networks(
                platform_env,
                dry_run=dry_run,
                network_inspect_fn=network_inspect_fn,
                network_create_fn=network_create_fn,
            )
        elif scope == "volumes":
            provision_volumes(platform_env, dry_run=dry_run, isdir_fn=isdir_fn, makedirs_fn=makedirs_fn)
        elif scope == "env":
            github_env = os.environ.get("GITHUB_ENV")
            provision_env(platform_env, dry_run=dry_run, github_env=github_env)
        elif scope == "profiles":
            provision_profiles(platform_env)
    except PlatformError as e:
        logger.critical("[IMP:10][main] Unhandled platform error (exit=%d): %s", e.exit_code, e)
        print(f"[FATAL] {e}", file=sys.stderr)
        return e.exit_code

    return 0


if __name__ == "__main__":
    sys.exit(main())
