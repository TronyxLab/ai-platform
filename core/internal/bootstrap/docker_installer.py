#!/usr/bin/env python3
# GREP_SUMMARY: install-docker docker_installer idempotent docker compose-plugin apt no-ports daemon.json live-restore verify
# STRUCTURE: ▶ guard(docker installed?) → skip | install_apt_deps → add_repo → install_packages → configure_daemon (merge/default) → systemd_override → enable → verify (docker+compose+no 2375/2376) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Idempotent installation of Docker Engine + Compose plugin from official apt repo;
##           enables live-restore. Python-порт install-docker.sh (DevPlan 118 E2).
## @scope    Called once during bootstrap phase φ1 (phases.py) via thin facade core/internal/bootstrap/install-docker.sh.
##           Safe to re-run on already-provisioned nodes.
## @invariants
##   - docker --version check prevents re-installation (guard)
##   - Docker daemon ports (2375/2376) are NEVER opened — verify fail-fast
##   - /etc/docker/daemon.json enforces live-restore=true for zero-downtime daemon restarts
##   - daemon.json merge — docker_daemon.merge_live_restore (atomic, TRAP[BUG] устранён)
##   - systemd override Restart=always RestartSec=10s (created only if absent)
## @rationale Docker manages its own iptables chains; we must not open docker ports in ufw.
##            Strangler E2: пакеты/daemon/verify — тестируемые pure functions (subprocess-оркестрация apt/systemd).
## @changes  2026-08-02 | DevPlan 118 E2 — Created (Python-порт install-docker.sh, 218 LOC)
## @see      core/internal/bootstrap/install-docker.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from core.internal.shared.timeouts import DOCKER_APT_TIMEOUT, DOCKER_CMD_TIMEOUT

logger = logging.getLogger(__name__)

APT_DEPS: tuple[str, ...] = ("ca-certificates", "curl", "gnupg", "lsb-release")
DOCKER_PACKAGES: tuple[str, ...] = (
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-buildx-plugin",
    "docker-compose-plugin",
)
DAEMON_JSON_DEFAULT: dict[str, object] = {
    "iptables": True,
    "ip-forward": True,
    "live-restore": True,
    "log-driver": "json-file",
    "log-opts": {"max-size": "50m", "max-file": "5"},
}
SYSTEMD_OVERRIDE = "[Service]\nRestart=always\nRestartSec=10s\n"
SCRIPT_DIR = Path(__file__).resolve().parent


# region FUNC_select_missing_packages
## @purpose  Отфильтровать пакеты, которых нет среди installed (dpkg-множество).
## @io       ⇥ candidates: tuple[str, ...], installed: set[str] → ⎋ list[str]
## @complexity O(P) — P = кандидаты
def select_missing_packages(candidates: tuple[str, ...], installed: set[str]) -> list[str]:
    """Return candidates not present in installed set."""
    return [pkg for pkg in candidates if pkg not in installed]


# endregion FUNC_select_missing_packages


# region FUNC_guard_already_installed
## @purpose  Guard: docker CLI или dpkg docker-ce уже установлены → skip установки.
## @io       ⇥ docker_version_out: str, dpkg_out: str → ⎋ bool (уже установлен)
## @complexity O(1)
def guard_already_installed(docker_version_out: str, dpkg_out: str) -> bool:
    """Return True if docker is already installed (CLI works OR docker-ce dpkg present)."""
    return bool(docker_version_out.strip()) or "docker-ce" in dpkg_out


# endregion FUNC_guard_already_installed


# region FUNC_build_daemon_repo_command
## @purpose  Построить команду добавления Docker apt-репозитория (keyring + list file + apt update).
## @io       ⇥ arch: str, codename: str, keyring: str, list_file: str → ⎋ list[str] — команды
## @complexity O(1)
def build_repo_command(arch: str, codename: str, keyring: str, list_file: str) -> list[str]:
    """Build the Docker apt repo list line (deb [arch=.. signed-by=..] https://download.docker.com/linux/ubuntu ..)."""
    return [
        "deb",
        f"[arch={arch} signed-by={keyring}]",
        "https://download.docker.com/linux/ubuntu",
        codename,
        "stable",
    ]


# endregion FUNC_build_daemon_repo_command


# region FUNC_verify_installation
## @purpose  Verify: docker --version, docker compose version, no 2375/2376 ports (ss output).
## @io       ⇥ docker_version_out: str, compose_version_out: str, ss_out: str → ⎋ tuple[bool, str]
## @complexity O(1)
def verify_installation(docker_version_out: str, compose_version_out: str, ss_out: str) -> tuple[bool, str]:
    """Verify docker+compose present and ports 2375/2376 not exposed. (ok, message)."""
    if not docker_version_out.strip():
        return False, "docker --version failed after installation"
    if "version" not in compose_version_out.lower() and not compose_version_out.strip():
        return False, "docker compose version failed — Compose plugin missing"
    if ":2375" in ss_out or ":2376" in ss_out:
        return False, "SECURITY: Docker API ports 2375/2376 are exposed — aborting"
    return True, f"Docker {docker_version_out.strip()} + Compose — ports secure"


# endregion FUNC_verify_installation


# region FUNC_configure_daemon
## @purpose  Настроить daemon.json: merge live-restore (существующий) ИЛИ записать дефолт.
## @io       ⇥ daemon_json: Path → ⎋ bool
## @complexity O(1) — файловая операция
def configure_daemon(daemon_json: Path, dry: bool = False) -> bool:
    """Configure daemon.json: merge live-restore if exists, else write default (live-restore: true)."""
    if dry:
        logger.info("[IMP:7][install-docker][daemon] dry-run: configure daemon.json at %s", daemon_json)
        return True
    if daemon_json.is_file():
        # Strangler 2026-07-31: docker_daemon.py merge-live-restore (atomic write, TRAP[BUG] устранён)
        from core.internal.bootstrap.docker_daemon import merge_live_restore

        ok = merge_live_restore(str(daemon_json))
        if ok:
            logger.info("[IMP:9][install-docker][daemon] live-restore: true merged into existing daemon.json")
        return ok
    try:
        import json

        daemon_json.write_text(json.dumps(DAEMON_JSON_DEFAULT, indent=2) + "\n", encoding="utf-8")
        logger.info("[IMP:9][install-docker][daemon] daemon.json written — live-restore: true enabled")
        return True
    except OSError as exc:
        logger.error("[IMP:10][install-docker][daemon] Cannot write %s: %s", daemon_json, exc)
        return False


# endregion FUNC_configure_daemon


# region FUNC_configure_systemd_override
## @purpose  Создать systemd override (Restart=always, RestartSec=10s), только если отсутствует.
## @io       ⇥ override_file: Path → ⎋ bool
## @complexity O(1)
def configure_systemd_override(override_file: Path, dry: bool = False) -> bool:
    """Write systemd override for docker.service (skip if exists)."""
    if dry:
        logger.info("[IMP:7][install-docker][systemd] dry-run: write override at %s", override_file)
        return True
    if override_file.is_file():
        logger.info("[IMP:8][install-docker][systemd] Override already exists at %s", override_file)
        return True
    try:
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(SYSTEMD_OVERRIDE, encoding="utf-8")
        logger.info("[IMP:9][install-docker][systemd] Systemd override written — Restart=always, RestartSec=10s")
        return True
    except OSError as exc:
        logger.error("[IMP:10][install-docker][systemd] Cannot write %s: %s", override_file, exc)
        return False


# endregion FUNC_configure_systemd_override


# region FUNC_run
## @purpose  Полный прогон установки (subprocess-оркестрация apt/systemd). Инъекция команд через env
##           DOCKER_INSTALLER_DRY_RUN=1 — тестируемость без реального apt/systemd.
## @io       ⇥ None → ⎋ bool
## @complexity O(1) — последовательность system-команд
def run() -> bool:
    """Full idempotent docker installation pipeline."""
    dry = os.environ.get("DOCKER_INSTALLER_DRY_RUN", "") == "1"
    docker_out = _sh("docker", "--version", dry=dry)
    dpkg_out = _sh("dpkg", "-s", "docker-ce", dry=dry)
    if guard_already_installed(docker_out, dpkg_out):
        logger.info(
            "[IMP:8][install-docker][guard] Docker already installed: %s", docker_out.strip() or "(dpkg docker-ce)"
        )
        if dry:
            logger.info("[IMP:9][install-docker][verify] dry-run: verify skipped (no docker on host)")
            return True
        verify_out = _sh("docker", "--version", dry=dry)
        compose_out = _sh("docker", "compose", "version", dry=dry)
        ss_out = _sh("ss", "-tlnp", dry=dry)
        ok, msg = verify_installation(verify_out, compose_out, ss_out)
        if not ok:
            logger.error("[IMP:10][install-docker][verify] %s", msg)
            return False
        logger.info("[IMP:9][install-docker][verify] %s", msg)
        return True

    # ── install apt deps (missing only) ──
    dpkg_all = _sh("dpkg", "-s", "ca-certificates", "curl", "gnupg", "lsb-release", dry=dry)
    installed = {pkg for pkg in APT_DEPS if pkg in dpkg_all}
    missing_deps = select_missing_packages(APT_DEPS, installed)
    if missing_deps:
        _sh("apt-get", "update", "-qq", dry=dry, timeout=DOCKER_APT_TIMEOUT)
        _sh("apt-get", "install", "-y", "-qq", *missing_deps, dry=dry, timeout=DOCKER_APT_TIMEOUT)
        logger.info("[IMP:8][install-docker][apt-deps] Installed: %s", " ".join(missing_deps))
    else:
        logger.info("[IMP:8][install-docker][apt-deps] All prerequisite packages already present")

    # ── add docker repo ──
    keyring = "/etc/apt/keyrings/docker.gpg"
    list_file = "/etc/apt/sources.list.d/docker.list"
    if not (Path(keyring).is_file() and Path(list_file).is_file()):
        _sh("install", "-m", "0755", "-d", "/etc/apt/keyrings", dry=dry)
        _sh(
            "bash",
            "-c",
            "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o " + keyring,
            dry=dry,
        )
        _sh("chmod", "a+r", keyring, dry=dry)
        arch = _sh("dpkg", "--print-architecture", dry=dry).strip()
        codename = _read_os_release(dry=dry)
        repo_line = " ".join(build_repo_command(arch, codename, keyring, list_file))
        _sh("bash", "-c", f"echo '{repo_line}' > {list_file}", dry=dry)
        _sh("apt-get", "update", "-qq", dry=dry, timeout=DOCKER_APT_TIMEOUT)
        logger.info("[IMP:8][install-docker][docker-repo] Docker apt repo configured for %s/%s", codename, arch)
    else:
        logger.info("[IMP:8][install-docker][docker-repo] Docker apt repo already configured")

    # ── install docker packages ──
    _sh("apt-get", "install", "-y", "-qq", *DOCKER_PACKAGES, dry=dry, timeout=DOCKER_APT_TIMEOUT)
    logger.info(
        "[IMP:8][install-docker][docker-install] Docker installed: %s", _sh("docker", "--version", dry=dry).strip()
    )

    # ── configure daemon + systemd override + enable ──
    if not configure_daemon(Path("/etc/docker/daemon.json"), dry=dry):
        return False
    if not configure_systemd_override(Path("/etc/systemd/system/docker.service.d/restart.conf"), dry=dry):
        return False
    _sh("systemctl", "daemon-reload", dry=dry)
    _sh("systemctl", "enable", "docker", "--quiet", dry=dry)
    _sh("systemctl", "start", "docker", dry=dry)

    # ── verify ──
    if dry:
        logger.info("[IMP:9][install-docker][verify] dry-run: verify skipped (no docker on host)")
        return True
    docker_out = _sh("docker", "--version", dry=dry)
    compose_out = _sh("docker", "compose", "version", dry=dry)
    ss_out = _sh("ss", "-tlnp", dry=dry)
    ok, msg = verify_installation(docker_out, compose_out, ss_out)
    if not ok:
        logger.error("[IMP:10][install-docker][verify] %s", msg)
        return False
    logger.info("[IMP:9][install-docker][verify] %s", msg)
    return True


# endregion FUNC_run


# region FUNC_sh
## @purpose  Выполнить команду; DOCKER_INSTALLER_DRY_RUN=1 → логировать и вернуть "".
## @io       ⇥ *args: str, dry: bool → ⎋ str (stdout)
## @complexity O(1)
def _sh(*args: str, dry: bool = False, timeout: int = DOCKER_CMD_TIMEOUT) -> str:
    """Run a subprocess command (dry-run → log and return '').

    ## @purpose — Команды docker-домена: DOCKER_CMD_TIMEOUT (10s). apt-get update/install
    ##            docker-пакетов передают DOCKER_APT_TIMEOUT (300s) — RC 121 e2e fix:
    ##            скачивание docker-ce на fresh VPS занимает >10s.
    """
    cmd = list(args)
    if dry:
        logger.info("[IMP:7][install-docker][dry] %s", " ".join(cmd))
        return ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("[IMP:7][install-docker][sh] command failed: %s (%s)", " ".join(cmd), exc)
        return ""
    return result.stdout or ""


# endregion FUNC_sh


# region FUNC_read_os_release
## @purpose  Прочитать VERSION_CODENAME из /etc/os-release.
## @io       ⇥ dry: bool → ⎋ str
## @complexity O(1)
def _read_os_release(dry: bool = False) -> str:
    """Read VERSION_CODENAME from /etc/os-release (fallback 'noble')."""
    if dry:
        return "noble"
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "noble"


# endregion FUNC_read_os_release


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 -m core.internal.bootstrap.docker_installer`.

    ▶ ┌env┐ → ○ run() → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return 0 if run() else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
