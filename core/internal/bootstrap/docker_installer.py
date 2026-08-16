#!/usr/bin/env python3
# GREP_SUMMARY: install-docker docker_installer idempotent docker compose-plugin apt no-ports daemon.json live-restore verify docker-user dropin default-address-pools
# STRUCTURE: ▶ guard(docker installed?) → skip | install_apt_deps → add_repo → install_packages → configure_daemon (merge/default) → systemd_override → enable → verify (docker+compose+no 2375/2376) → ⎋ exit 0|1 ┤
#            ▶ W2-3: docker.service ExecStartPost drop-in → firewall.py --apply-docker-user (DOCKER-USER policy после старта daemon)
# region MODULE_CONTRACT
## @purpose  Idempotent installation of Docker Engine + Compose plugin from official apt repo;
##           enables live-restore. Python-порт install-docker.sh (DevPlan 118 E2).
##           DevPlan 162: W5-2 default-address-pools (10.32.0.0/16, size 24) в daemon.json —
##           встроенный пул 172.17-31 исчерпан; W2-3 persistence DOCKER-USER политики через
##           systemd drop-in docker.service ExecStartPost (firewall.py --apply-docker-user).
## @scope    Called once during bootstrap phase φ1 (phases.py) via thin facade core/internal/bootstrap/install-docker.sh.
##           Safe to re-run on already-provisioned nodes.
## @invariants
##   - docker --version check prevents re-installation (guard)
##   - Docker daemon ports (2375/2376) are NEVER opened — verify fail-fast
##   - /etc/docker/daemon.json enforces live-restore=true for zero-downtime daemon restarts
##   - daemon.json merge — docker_daemon.merge_live_restore (atomic)
##   - systemd override Restart=always RestartSec=10s (created only if absent)
##   - W2-3: drop-in 99-platform-docker-user.conf (ExecStartPost firewall.py --apply-docker-user)
##     создаётся только если отсутствует (идемпотентно); DOCKER-USER политика применяется
##     ПОСЛЕ старта docker (цепочка существует) — см. TRAP[DECISION] ниже
##   - W5-2: default-address-pools {"base":"10.32.0.0/16","size":24} — не пересекается с
##     TOR_PRIVOXY_NET 172.16.0.0/12 (firewall.py) и встроенными 172.17-31/16
## @rationale Docker manages its own iptables chains; we must not open docker ports in ufw.
##            Strangler E2: пакеты/daemon/verify — тестируемые pure functions (subprocess-оркестрация apt/systemd).
## @changes  2026-08-02 | DevPlan 118 E2 — Created (Python-порт install-docker.sh, 218 LOC)
## @changes  2026-08-13 | DevPlan 162 W2-3/W5-2 — +docker-user ExecStartPost drop-in; +default-address-pools
## @see      core/internal/bootstrap/install-docker.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# DevPlan 119 B2/B3 канон: /opt/platform литерал запрещён (гейт no_hardcoded_local_paths) —
# remote-база резолвится через deploy_paths.platform_remote_base() (PLATFORM_REMOTE_BASE).
from core.internal.shared.deploy_paths import platform_remote_base
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
# W5-2 (DevPlan 162): default-address-pools — встроенный пул 172.17-31/16 исчерпан;
# проектные сети 192.168.x/20 не зафиксированы нигде → нода непересоздаваема, pg_hba/ufw
# завязаны на подсети. 10.32.0.0/16 не пересекается с TOR_PRIVOXY_NET=172.16.0.0/12
# (firewall.py) и docker default 172.17-31; size 24 = до 256 /24-сетей на ноду.
DEFAULT_ADDRESS_POOLS: list[dict[str, object]] = [{"base": "10.32.0.0/16", "size": 24}]
DAEMON_JSON_DEFAULT: dict[str, object] = {
    "iptables": True,
    "ip-forward": True,
    "live-restore": True,
    "log-driver": "json-file",
    "log-opts": {"max-size": "50m", "max-file": "5"},
    "default-address-pools": DEFAULT_ADDRESS_POOLS,
}
SYSTEMD_OVERRIDE = "[Service]\nRestart=always\nRestartSec=10s\n"
# W2-3 (DevPlan 162): persistence DOCKER-USER политики. Docker 20.10+ пересоздаёт iptables
# цепочки при КАЖДОМ рестарте daemon — статический iptables-restore сервис не переживает
# (цепочки создаются docker'ом после restore). ExecStartPost обязателен: применяется сразу
# после старта daemon (цепочка DOCKER-USER уже существует).
# ⚠️ ПРЕДУСЛОВИЕ (DevPlan 162 W2-3): политика применяется ТОЛЬКО после port-audit W2-2
# (compose-gate + converge-миграция bindings) — иначе catch-all DROP отрежет легитимный
# ingress user-проектов, публикующих порты вне {80,443}.
_FIREWALL_SCRIPT = str(Path(str(platform_remote_base())) / "core" / "internal" / "bootstrap" / "firewall.py")
DOCKER_USER_DROPIN_DIR = "/etc/systemd/system/docker.service.d"
DOCKER_USER_DROPIN_FILE = "99-platform-docker-user.conf"
# v1.0.1 TRAP[BUG] (Фаза 6, bootstrap tronyx-vps): ExecStartPost вызывал firewall.py БЕЗ
# PYTHONPATH → «No module named 'core'» → Control process exit 1 → systemd помечал
# docker.service FAILED (daemon жив, но сервис failed → φ8 precondition «Docker daemon
# running» RED). Фикс: Environment=PYTHONPATH=<core-корень> в том же drop-in.
_FIREWALL_PYTHONPATH = str(Path(_FIREWALL_SCRIPT).resolve().parents[3])
DOCKER_USER_DROPIN_CONTENT = (
    "[Service]\n"
    f"Environment=PYTHONPATH={_FIREWALL_PYTHONPATH}\n"
    f"ExecStartPost=/usr/bin/python3 {_FIREWALL_SCRIPT} --apply-docker-user\n"
)
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
def build_repo_command(arch: str, codename: str, keyring: str, _list_file: str) -> list[str]:
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
        # Strangler 2026-07-31: docker_daemon.py merge-live-restore (atomic write)
        from core.internal.bootstrap.docker_daemon import merge_live_restore

        ok = merge_live_restore(str(daemon_json))
        if ok:
            logger.info("[IMP:9][install-docker][daemon] live-restore: true merged into existing daemon.json")
        return ok
    try:
        import json

        daemon_json.write_text(json.dumps(DAEMON_JSON_DEFAULT, indent=2) + "\n", encoding="utf-8")
        logger.info("[IMP:9][install-docker][daemon] daemon.json written — live-restore: true enabled")
    except OSError as exc:
        logger.error("[IMP:10][install-docker][daemon] Cannot write %s: %s", daemon_json, exc)
        return False
    else:
        return True


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
    except OSError as exc:
        logger.error("[IMP:10][install-docker][systemd] Cannot write %s: %s", override_file, exc)
        return False
    else:
        return True


# endregion FUNC_configure_systemd_override


# region FUNC_configure_docker_user_dropin
## @purpose  Создать systemd drop-in docker.service ExecStartPost (DOCKER-USER политика, DevPlan
##           162 W2-3), только если отсутствует (идемпотентно, паттерн configure_systemd_override).
## @io       ⇥ dropin: Path | None = None → ⎋ bool
## @complexity O(1)
## @invariants  Существующий drop-in НЕ перезаписывается (idempotent, канон W2-3)
##              ExecStartPost: firewall.py --apply-docker-user — применяется после старта daemon
##              (цепочка DOCKER-USER существует; iptables-restore не переживает Docker 20.10+)
## @rationale  Держим в docker_installer (не в firewall): жизненный цикл docker.service —
##             здесь же configure_systemd_override/Requires-связи (W3-1); firewall.py —
##             чистая политика + apply.
def configure_docker_user_dropin(dropin: Path | None = None, dry: bool = False) -> bool:
    """Write docker.service ExecStartPost drop-in for DOCKER-USER policy (skip if exists)."""
    target = dropin or Path(DOCKER_USER_DROPIN_DIR) / DOCKER_USER_DROPIN_FILE
    if dry:
        logger.info("[IMP:7][install-docker][docker-user] dry-run: write drop-in at %s", target)
        return True
    if target.is_file():
        logger.info("[IMP:8][install-docker][docker-user] DOCKER-USER drop-in already exists at %s", target)
        return True
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(DOCKER_USER_DROPIN_CONTENT, encoding="utf-8")
        logger.info(
            "[IMP:9][install-docker][docker-user] Drop-in written — ExecStartPost DOCKER-USER policy on daemon start"
        )
    except OSError as exc:
        logger.error("[IMP:10][install-docker][docker-user] Cannot write %s: %s", target, exc)
        return False
    else:
        return True


# endregion FUNC_configure_docker_user_dropin

# 🧐 TRAP[DECISION] · 2026-08-13 · MED · DOCKER-USER применяется ExecStartPost, не iptables-restore
# · Rejected: netfilter-persistent / iptables-restore сервис (правила переживают reboot «на бумаге»)
# · Reason: Docker 20.10+ пересоздаёт DOCKER-USER/DOCKER цепочки при КАЖДОМ старте daemon —
# ·   iptables-restore применяется РАНЬШЕ docker и его правила сбрасываются; ExecStartPost
# ·   гарантирует применение ПОСЛЕ создания цепочек (DevPlan 162 W2-3). Предусловие —
# ·   port-audit W2-2 завершён (иначе DROP отрежет легитимный ingress user-проектов).
# · Rev: если Docker начнёт переживать внешние DOCKER-USER правила (persistent chains) —
# ·   перейти на netfilter-persistent


# region FUNC_run
## @purpose  Полный прогон установки (subprocess-оркестрация apt/systemd). Инъекция команд через env
##           DOCKER_INSTALLER_DRY_RUN=1 — тестируемость без реального apt/systemd.
## @io       ⇥ sh_fn: Callable | None (167 D0 DI: _sh-канал; None → модульный _sh) → ⎋ bool
## @complexity O(1) — последовательность system-команд
def run(sh_fn: Callable[..., str] | None = None) -> bool:
    """Full idempotent docker installation pipeline."""
    # 🧐 TRAP[DI-SEAM] · 2026-08-14 · — · sh_fn-параметр: команды apt/systemd через DI вместо
    # ·   monkeypatch docker_installer._sh (DevPlan 167 D0)
    # · Rejected: прямой вызов _sh (тест не мог бы перехватить guard-пробу без subprocess)
    # · Reason: sh_fn=None → модульный _sh (поведение без изменений); тест передаёт fake
    # ·   (guard-probe docker --version → «уже установлен», install-команды записываются)
    # · Rev: при переходе на CommandRunner-протокол (shared/subprocess_io) — синхронизировать
    sh_impl = sh_fn if sh_fn is not None else _sh
    dry = os.environ.get("DOCKER_INSTALLER_DRY_RUN", "") == "1"
    docker_out = sh_impl("docker", "--version", dry=dry)
    dpkg_out = sh_impl("dpkg", "-s", "docker-ce", dry=dry)
    if guard_already_installed(docker_out, dpkg_out):
        logger.info(
            "[IMP:8][install-docker][guard] Docker already installed: %s", docker_out.strip() or "(dpkg docker-ce)"
        )
        if dry:
            logger.info("[IMP:9][install-docker][verify] dry-run: verify skipped (no docker on host)")
            return True
        verify_out = sh_impl("docker", "--version", dry=dry)
        compose_out = sh_impl("docker", "compose", "version", dry=dry)
        ss_out = sh_impl("ss", "-tlnp", dry=dry)
        ok, msg = verify_installation(verify_out, compose_out, ss_out)
        if not ok:
            logger.error("[IMP:10][install-docker][verify] %s", msg)
            return False
        logger.info("[IMP:9][install-docker][verify] %s", msg)
        return True

    # ── install apt deps (missing only) ──
    dpkg_all = sh_impl("dpkg", "-s", "ca-certificates", "curl", "gnupg", "lsb-release", dry=dry)
    installed = {pkg for pkg in APT_DEPS if pkg in dpkg_all}
    missing_deps = select_missing_packages(APT_DEPS, installed)
    if missing_deps:
        sh_impl("apt-get", "update", "-qq", dry=dry, timeout=DOCKER_APT_TIMEOUT)
        sh_impl("apt-get", "install", "-y", "-qq", *missing_deps, dry=dry, timeout=DOCKER_APT_TIMEOUT)
        logger.info("[IMP:8][install-docker][apt-deps] Installed: %s", " ".join(missing_deps))
    else:
        logger.info("[IMP:8][install-docker][apt-deps] All prerequisite packages already present")

    # ── add docker repo ──
    keyring = "/etc/apt/keyrings/docker.gpg"
    list_file = "/etc/apt/sources.list.d/docker.list"
    if not (Path(keyring).is_file() and Path(list_file).is_file()):
        sh_impl("install", "-m", "0755", "-d", "/etc/apt/keyrings", dry=dry)
        sh_impl(
            "bash",
            "-c",
            "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o " + keyring,
            dry=dry,
        )
        sh_impl("chmod", "a+r", keyring, dry=dry)
        arch = sh_impl("dpkg", "--print-architecture", dry=dry).strip()
        codename = _read_os_release(dry=dry)
        repo_line = " ".join(build_repo_command(arch, codename, keyring, list_file))
        sh_impl("bash", "-c", f"echo '{repo_line}' > {list_file}", dry=dry)
        sh_impl("apt-get", "update", "-qq", dry=dry, timeout=DOCKER_APT_TIMEOUT)
        logger.info("[IMP:8][install-docker][docker-repo] Docker apt repo configured for %s/%s", codename, arch)
    else:
        logger.info("[IMP:8][install-docker][docker-repo] Docker apt repo already configured")

    # ── install docker packages ──
    sh_impl("apt-get", "install", "-y", "-qq", *DOCKER_PACKAGES, dry=dry, timeout=DOCKER_APT_TIMEOUT)
    logger.info(
        "[IMP:8][install-docker][docker-install] Docker installed: %s", sh_impl("docker", "--version", dry=dry).strip()
    )

    # ── configure daemon + systemd override + enable ──
    if not configure_daemon(Path("/etc/docker/daemon.json"), dry=dry):
        return False
    if not configure_systemd_override(Path("/etc/systemd/system/docker.service.d/restart.conf"), dry=dry):
        return False
    if not configure_docker_user_dropin(dry=dry):
        # W2-3: fail-fast как configure_systemd_override — drop-in не записан = DOCKER-USER
        # политика не переживёт следующий рестарт daemon (молчаливый security-gap).
        return False
    sh_impl("systemctl", "daemon-reload", dry=dry)
    sh_impl("systemctl", "enable", "docker", "--quiet", dry=dry)
    sh_impl("systemctl", "start", "docker", dry=dry)

    # ── verify ──
    if dry:
        logger.info("[IMP:9][install-docker][verify] dry-run: verify skipped (no docker on host)")
        return True
    docker_out = sh_impl("docker", "--version", dry=dry)
    compose_out = sh_impl("docker", "compose", "version", dry=dry)
    ss_out = sh_impl("ss", "-tlnp", dry=dry)
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
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
