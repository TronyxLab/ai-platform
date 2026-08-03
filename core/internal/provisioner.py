#!/usr/bin/env python3
# GREP_SUMMARY: provision, platform-env, docker-network, volume-dir, ci-env, idempotent, dry-run
# STRUCTURE: ▶ cli:args→dispatch → ◇ load_platform_env→PlatformEnv → ⊕ provision_networks(subprocess docker) → ⊕ provision_volumes(mkdir -p) → ⊕ provision_env(GITHUB_ENV|stderr) → ⊕ provision_profiles → ⎋ exit 0|1|10
# region MODULE_CONTRACT
## @purpose  Python provisioner — replaces 13 inline python3 calls in provision-environment.sh.
##           Reads platform-env.yaml, creates Docker networks, volume directories, CI env vars.
## @scope    Called from provision-environment.sh (shell wrapper) with --scope arg.
##           Single scope per invocation. Shell wrapper iterates scopes + wraps audit_step.
## @invariants
##   - LDD block name: [provision] (NOT [provisioner]) — backward compat with 30 test assertions
##   - Networks: docker inspect → exists → skip, else docker network create
##   - Volumes: os.path.isdir → exists → skip, else os.makedirs(exist_ok=True)
##   - Env: GITHUB_ENV from os.environ → write KEY=VALUE; None → print to stderr
##   - Exit codes: 0=success, 1=parse error, 10=docker unavailable (PlatformFatalError, D4)
## @rationale  Eliminates YAML→JSON→shell→python3 round-trips, single YAML parse per scope.
##             All business logic testable natively (no subprocess for JSON parsing).
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.internal.shared.exceptions import PlatformError, PlatformFatalError

# ── Logger setup ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
_ch = logging.StreamHandler(sys.stderr)
_ch.setFormatter(logging.Formatter("%(message)s"))
logger.handlers = [_ch]


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class NetworkConfig:
    """Single Docker network definition from platform-env.yaml."""

    name: str
    driver: str = "bridge"
    internal: bool = False


@dataclass
class VolumeConfig:
    """Single volume directory definition from platform-env.yaml.

    ## @purpose — Host directory (bind-mount source) + optional владелец.
    ## @invariants
    ##   - owner: "uid:gid" или "" — применяется chown при создании/несовпадении
    ##   - Постgres wal-archive требует владельца postgres (999:999) — TRAP[BUG] 2026-08-03
    """

    path: str
    owner: str = ""


@dataclass
class PlatformEnv:
    """Parsed platform-env.yaml structure."""

    networks: list[NetworkConfig]
    volumes: list[VolumeConfig]
    env_defaults: dict[str, str]
    profiles: list[str]


@dataclass
class ProvisionResult:
    """Result of a single scope provision operation."""

    scope: str
    created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── YAML Loading ──────────────────────────────────────────────────────────────


def load_platform_env(yaml_path: Path) -> PlatformEnv:
    """Parse platform-env.yaml into typed PlatformEnv.

    Reads YAML via PyYAML. Extracts 4 sections: networks, volumes,
    env_defaults, profiles. Handles missing sections gracefully (empty lists/dicts).

    Raises:
        FileNotFoundError: yaml_path does not exist
        yaml.YAMLError: malformed YAML
    """
    if not yaml_path.is_file():
        raise FileNotFoundError(yaml_path)

    logger.info("[IMP:7][provision] Reading platform-env.yaml from %s", yaml_path)

    with open(yaml_path) as f:
        data = yaml.safe_load(f) or {}

    networks_raw = data.get("networks") or []
    networks = [
        NetworkConfig(
            name=n["name"],
            driver=n.get("driver", "bridge"),
            internal=n.get("internal", False),
        )
        for n in networks_raw
    ]

    volumes_raw = data.get("volumes") or []
    volumes = [
        VolumeConfig(path=v["path"], owner=str(v.get("owner", "") or "").strip())
        for v in volumes_raw
    ]

    env_defaults = dict(data.get("env_defaults") or {})
    profiles = list(data.get("profiles") or [])

    result = PlatformEnv(
        networks=networks,
        volumes=volumes,
        env_defaults=env_defaults,
        profiles=profiles,
    )

    logger.info(
        "[IMP:8][provision] Parsed: %d networks, %d volumes, %d env vars, %d profiles",
        len(result.networks),
        len(result.volumes),
        len(result.env_defaults),
        len(result.profiles),
    )

    return result


# ── Scope: Networks ───────────────────────────────────────────────────────────


def provision_networks(
    platform_env: PlatformEnv,
    dry_run: bool = False,
) -> ProvisionResult:
    """Create Docker networks from platform-env.networks.

    IDEMPOTENT: docker network inspect → exists → skip, else docker network create.
    Uses subprocess.run for docker commands.
    """
    result = ProvisionResult(scope="networks")

    logger.info("[IMP:7][provision][networks] Reading platform-env.yaml networks")

    if not platform_env.networks:
        logger.info("[IMP:8][provision][networks] No networks defined — nothing to do")
        return result

    if not dry_run and not shutil.which("docker"):
        logger.error("[IMP:10][provision][networks] FATAL: Docker is not available")
        # D4 (DevPlan 116 B4): docker unavailable — невосстановимо без ручного действия → PlatformFatalError (10).
        # Ранее sys.exit(2); shell-фасады обновлены (provision.mk/CI) — exit-код docker-unavailable теперь 10.
        raise PlatformFatalError("Docker is not available — provision networks requires docker")

    for net in platform_env.networks:
        if dry_run:
            logger.info(
                "[IMP:7][provision][networks] DRY-RUN: Would create network: %s (driver: %s)",
                net.name,
                net.driver,
            )
            result.created += 1
            continue

        # Check if network exists
        inspect_rc = subprocess.run(
            ["docker", "network", "inspect", net.name],
            capture_output=True,
            text=True,
            check=False,
        ).returncode

        if inspect_rc == 0:
            logger.info("[IMP:7][provision][networks] SKIP: network %s already exists", net.name)
            result.skipped += 1
        else:
            logger.info("[IMP:7][provision][networks] Creating network: %s (driver: %s)", net.name, net.driver)
            create = subprocess.run(
                ["docker", "network", "create", "--driver", net.driver, net.name],
                capture_output=True,
                text=True,
                check=False,
            )
            if create.returncode != 0:
                msg = f"Failed to create network {net.name}: {create.stderr.strip()}"
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
) -> ProvisionResult:
    """Create volume directories from platform-env.volumes.

    IDEMPOTENT: os.path.isdir → exists → skip, else mkdir -p.
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

        if os.path.isdir(vol_path):
            logger.info("[IMP:7][provision][volumes] SKIP: directory already exists: %s", vol_path)
            result.skipped += 1
            if vol.owner and not _owner_matches(vol_path, vol.owner):
                _chown_dir(vol_path, vol.owner, result)
        else:
            logger.info("[IMP:7][provision][volumes] Creating directory: %s", vol_path)
            try:
                os.makedirs(vol_path, exist_ok=True)
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
        with open(github_env, "a") as f:
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


def main() -> int:
    """CLI entry point: python3 provisioner.py --scope <scope> --platform-env <path> [--dry-run]

    Exit codes:
        0 — success (all resources created or already exist)
        1 — parse error (YAML invalid, file not found, unknown scope)
        10 — docker unavailable (for --scope networks) — PlatformFatalError (D4, DevPlan 116 B4)
    """
    parser = argparse.ArgumentParser(
        description="Idempotent environment provisioner — reads platform-env.yaml",
    )
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

    args = parser.parse_args()

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

    try:
        if scope == "networks":
            provision_networks(platform_env, dry_run=dry_run)
        elif scope == "volumes":
            provision_volumes(platform_env, dry_run=dry_run)
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
