#!/usr/bin/env python3
# GREP_SUMMARY: platform-secrets installer systemd oneshot decrypt sops age secrets env boot tmpfs install enable age-key-migration
# STRUCTURE: guard(root) → check_prerequisites (age-key create/migrate/perm-fix + secrets-enc symlink-fallback) → ensure_platform_dirs (setgid 2775) → install_service (cp unit + daemon-reload + enable) → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Idempotent systemd oneshot service installation for platform secrets decryption on boot.
##           Python-порт platform-secrets/install.sh (DevPlan 118 E7).
## @scope    Called during bootstrap deploy of system-type modules (invoke_module_interface install)
##           via thin facade core/modules/platform-secrets/install.sh; generates /var/lib/platform/run/secrets.env (142 W2).
## @invariants
##   - Service runs before docker.service (Before=docker.service in unit file)
##   - Service does NOT start during install — runs on next boot (Type=oneshot, RemainAfterExit=yes)
##   - /etc/platform/age-key.txt: создаётся из AGE_SECRET_KEY (KEY=VALUE EnvironmentFile format),
##     permission auto-fix (root:root 600), bare→KEY=VALUE миграция (B3 fix)
##   - secrets.enc.yaml: ${PLATFORM_ROOT}/secrets/ + /opt/node-configs/secrets/ symlink-fallback
##   - ensure_platform_dirs: ${PLATFORM_ROOT}/ + prometheus-targets + secrets, setgid 2775
## @rationale Secrets must be available before Docker starts so containers receive env vars from
##   persistent secrets.env (/var/lib/platform/run, 142 W2 — tmpfs /run/platform не переживал reboot);
##   systemd oneshot guarantees ordering without blocking boot.
##   Strangler E7: file-менеджмент + systemd → тестируемые pure functions (subprocess systemctl).
## @changes  2026-08-02 | DevPlan 118 E7 — Created (Python-порт platform-secrets/install.sh, 225 LOC)
## @see      core/modules/platform-secrets/install.sh (тонкий фасад)
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

AGE_KEY_PATH = Path("/etc/platform/age-key.txt")
SECRETS_ENV_PREFIX = "AGE_SECRET_KEY"
UNIT_NAME = "platform-secrets.service"
SYSTEMD_DIR = Path("/etc/systemd/system")
NODE_SECRETS_DIR = Path("/opt/node-configs/secrets")
# Локальный таймаут systemctl/chown (modules/ НЕ импортирует internal/ — cross-layer gate #8)
_SYSTEMCTL_TIMEOUT = 10
# 🧐 TRAP[DECISION] · 2026-08-14 · — · Дубль deploy_paths.platform_remote_base — cross-layer
# (modules↔internal запрещён) · Rejected: импорт core.internal.shared.deploy_paths
# · Reason: модульный код (core/modules) не может импортировать core/internal (cross-layer gate);
# ·   env PLATFORM_ROOT приоритетнее — константа только дефолт (тот же канон /opt/platform).
# · Rev: платформа начнёт инжектить PLATFORM_ROOT через platform-env для модулей — константа удаляется
_PLATFORM_ROOT_DEFAULT = "/opt/platform"


# region FUNC_ensure_age_key
## @purpose  Обеспечить /etc/platform/age-key.txt: создать из AGE_SECRET_KEY (KEY=VALUE format),
##           auto-fix permissions (root:root 600), мигрировать bare → KEY=VALUE.
## @io       ⇥ age_key: Path, env: dict[str, str] → ⎋ tuple[bool, str] — (ok, message)
## @complexity O(1) — файловые операции
## @invariants
##   - EnvironmentFile requires KEY=VALUE (B3 fix — bare key silently ignored systemd)
##   - Bare-формат (первая строка без 'AGE_SECRET_KEY=') → миграция в KEY=VALUE
def ensure_age_key(age_key: Path, env: dict[str, str]) -> tuple[bool, str]:
    """Create/verify age key file in EnvironmentFile KEY=VALUE format."""
    if not age_key.is_file():
        secret = env.get("AGE_SECRET_KEY", "")
        if not secret:
            return False, f"Age key file not found: {age_key} and AGE_SECRET_KEY not set"
        age_key.parent.mkdir(parents=True, exist_ok=True)
        age_key.write_text(f"{SECRETS_ENV_PREFIX}={secret}\n", encoding="utf-8")
        age_key.chmod(0o600)
        logger.info(
            "[IMP:9][platform-secrets][prereqs] Age key file created: %s (mode 600, EnvironmentFile format)", age_key
        )
        return True, "created"

    # ── permissions auto-fix (idempotent repeat-run) ──
    owner = _owner_of(age_key)
    mode = _mode_of(age_key)
    if owner != "root:root":
        logger.info("[IMP:8][platform-secrets][prereqs] Age key owner is %s, fixing to root:root", owner)
        _chown(age_key)
    if mode != "600":
        logger.info("[IMP:8][platform-secrets][prereqs] Age key mode is %s, fixing to 600", mode)
        age_key.chmod(0o600)

    # ── bare → KEY=VALUE migration (B3) ──
    first = age_key.read_text(encoding="utf-8").splitlines()[0] if age_key.read_text(encoding="utf-8").strip() else ""
    if first and not first.startswith(f"{SECRETS_ENV_PREFIX}="):
        bare_key = first.strip("\n\r")
        age_key.write_text(f"{SECRETS_ENV_PREFIX}={bare_key}\n", encoding="utf-8")
        age_key.chmod(0o600)
        logger.info("[IMP:9][platform-secrets][prereqs] Age key file migrated to EnvironmentFile KEY=VALUE format")
        return True, "migrated"
    logger.info("[IMP:9][platform-secrets][prereqs] Age key file: %s (owner: %s, mode: %s)", age_key, owner, mode)
    return True, "ok"


# endregion FUNC_ensure_age_key


# region FUNC_ensure_secrets_enc
## @purpose  Обеспечить ${PLATFORM_ROOT}/secrets/secrets.enc.yaml: проверка + node-configs symlink-fallback.
## @io       ⇥ secrets_enc: Path → ⎋ tuple[bool, str]
## @complexity O(1) — файловые операции
def ensure_secrets_enc(secrets_enc: Path, *, node_secrets_dir: Path | None = None) -> tuple[bool, str]:
    """Ensure encrypted secrets file exists; fallback: symlink from /opt/node-configs/secrets/*.enc.yaml."""
    node_dir = NODE_SECRETS_DIR if node_secrets_dir is None else node_secrets_dir
    if secrets_enc.is_file():
        logger.info("[IMP:8][platform-secrets][prereqs] Encrypted secrets file found: %s", secrets_enc)
        return True, "ok"
    if node_dir.is_dir():
        for candidate in sorted(node_dir.glob("*.enc.yaml")):
            secrets_enc.parent.mkdir(parents=True, exist_ok=True)
            if secrets_enc.is_symlink() or not secrets_enc.exists():
                with contextlib.suppress(OSError):
                    secrets_enc.unlink(missing_ok=True)
                secrets_enc.symlink_to(candidate)
            logger.info("[IMP:9][platform-secrets][prereqs] Symlink created: %s → %s", secrets_enc, candidate)
            return True, "symlinked"
    return False, f"Encrypted secrets file not found: {secrets_enc} (checked /opt/node-configs/secrets/*.enc.yaml too)"


# endregion FUNC_ensure_secrets_enc


# region FUNC_check_prerequisites
## @purpose  Полная проверка предпосылок: age-key + secrets-enc (fail-fast при отсутствии).
## @io       ⇥ platform_root: Path, env: dict[str, str], age_key_path: Path | None = None,
##              node_secrets_dir: Path | None = None (DI, W-H DevPlan 163 — override путей;
##              None = канонические AGE_KEY_PATH/NODE_SECRETS_DIR) → ⎋ bool
## @complexity O(1)
## @invariants
##   - DI: age_key_path/node_secrets_dir=None → модульные константы (поведение без изменений);
##     тесты передают tmp_path вместо monkeypatch.setattr констант
def check_prerequisites(
    platform_root: Path,
    env: dict[str, str],
    age_key_path: Path | None = None,
    node_secrets_dir: Path | None = None,
) -> bool:
    """Validate all prerequisites (age key + secrets file). False → abort (fail-fast)."""
    age_key = AGE_KEY_PATH if age_key_path is None else age_key_path
    node_dir = NODE_SECRETS_DIR if node_secrets_dir is None else node_secrets_dir
    ok_age, msg_age = ensure_age_key(age_key, env)
    if not ok_age:
        logger.error("[IMP:10][platform-secrets][prereqs] ABORT: %s", msg_age)
        return False
    ok_enc, msg_enc = ensure_secrets_enc(platform_root / "secrets" / "secrets.enc.yaml", node_secrets_dir=node_dir)
    if not ok_enc:
        logger.error("[IMP:10][platform-secrets][prereqs] ABORT: %s", msg_enc)
        return False
    logger.info("[IMP:9][platform-secrets][prereqs] DONE: all prerequisites satisfied")
    return True


# endregion FUNC_check_prerequisites


# region FUNC_ensure_platform_dirs
## @purpose  Создать ${PLATFORM_ROOT}/ структуру (setgid 2775, prometheus-targets, secrets).
## @io       ⇥ platform_root: Path → ⎋ bool
## @complexity O(1)
# region FUNC__plw_body_ensure_platform_dirs
## @purpose  Тело try-блока (PLW0717 extraction из ensure_platform_dirs) — семантика except не меняется.
## @io       ⇥ platform_root → ⎋ результат try-тела
## @complexity O(1) — извлечение управляющего потока
def _plw_body_ensure_platform_dirs(platform_root: Path) -> None:
    prometheus_dir = platform_root / "prometheus-targets"
    secrets_dir = platform_root / "secrets"
    prometheus_dir.mkdir(parents=True, exist_ok=True)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    platform_gid = _platform_gid()
    for d in (platform_root, prometheus_dir, secrets_dir):
        if platform_gid:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(["chown", f"root:{platform_gid}", str(d)], check=False, timeout=_SYSTEMCTL_TIMEOUT)
        d.chmod(0o2775)
    logger.info("[IMP:9][platform-secrets][dirs] Platform dirs ready: %s", platform_root)


# endregion FUNC__plw_body_ensure_platform_dirs


def ensure_platform_dirs(platform_root: Path) -> bool:
    """Create platform dir structure with platform-group write access (setgid 2775)."""
    try:
        _plw_body_ensure_platform_dirs(platform_root)
    except OSError as exc:
        logger.error("[IMP:10][platform-secrets][dirs] Cannot create platform dirs: %s", exc)
        return False
    else:
        return True


# endregion FUNC_ensure_platform_dirs


# region FUNC_install_service
## @purpose  Установить systemd unit: cp unit → daemon-reload → enable (НЕ стартует — oneshot на boot).
## @io       ⇥ unit_src: Path, systemd_dir: Path | None = None, unit_name: str | None = None,
##              systemctl_fn: Callable | None = None (DI, W-H DevPlan 163 — override
##              SYSTEMD_DIR/UNIT_NAME/_systemctl; None = канонические) → ⎋ bool
## @complexity O(1)
## @invariants
##   - DI: systemd_dir/unit_name/systemctl_fn=None → модульные константы/функция
##     (поведение без изменений); тесты передают tmp_path/fake-systemctl вместо патчей
def install_service(
    unit_src: Path,
    *,
    systemd_dir: Path | None = None,
    unit_name: str | None = None,
    systemctl_fn: Callable[..., object] | None = None,
) -> bool:
    """Copy systemd unit, daemon-reload, enable (does NOT start — runs on boot)."""
    target_dir = SYSTEMD_DIR if systemd_dir is None else systemd_dir
    target_name = UNIT_NAME if unit_name is None else unit_name
    systemctl = _systemctl if systemctl_fn is None else systemctl_fn
    if not unit_src.is_file():
        logger.error("[IMP:10][platform-secrets][install] Unit file not found at source: %s", unit_src)
        return False
    unit_dst = target_dir / target_name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        unit_dst.write_bytes(unit_src.read_bytes())
        unit_dst.chmod(0o644)
        systemctl("daemon-reload")
        systemctl("enable", f"{target_name}", "--quiet")
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error("[IMP:10][platform-secrets][install] systemd install failed: %s", exc)
        return False
    logger.info("[IMP:9][platform-secrets][install] DONE: %s installed and enabled (next boot)", target_name)
    return True


# endregion FUNC_install_service


# region FUNC_run
## @purpose  Полный прогон: prereqs → dirs → service. PLATFORM_ROOT из env (канон /opt/platform).
## @io       ⇥ env: dict[str, str] | None, age_key_path: Path | None = None (DI, W-H),
##              install_fn: Callable | None = None (DI — install_service; None = канон) → ⎋ bool
## @complexity O(1)
def run(
    env: dict[str, str] | None = None,
    *,
    age_key_path: Path | None = None,
    install_fn: Callable[[Path], bool] | None = None,
) -> bool:
    """Full platform-secrets installation pipeline (prereqs → dirs → systemd service)."""
    eff_env = dict(os.environ) if env is None else env
    platform_root = Path(eff_env.get("PLATFORM_ROOT", _PLATFORM_ROOT_DEFAULT))
    if not check_prerequisites(platform_root, eff_env, age_key_path=age_key_path):
        return False
    if not ensure_platform_dirs(platform_root):
        return False
    unit_src = Path(__file__).resolve().parent / "platform-secrets.service"
    installer = install_service if install_fn is None else install_fn
    return installer(unit_src)


# endregion FUNC_run


# region FUNC_helpers
def _owner_of(path: Path) -> str:
    """Return 'user:group' of path (stat)."""
    try:
        st = path.stat()
        import grp
        import pwd

        return f"{pwd.getpwuid(st.st_uid).pw_name}:{grp.getgrgid(st.st_gid).gr_name}"
    except (OSError, KeyError):
        return "unknown"


def _mode_of(path: Path) -> str:
    """Return octal mode of path (e.g. '600')."""
    try:
        return oct(path.stat().st_mode & 0o777).replace("0o", "")
    except OSError:
        return "unknown"


def _chown(path: Path) -> None:
    """chown root:root (best-effort subprocess — локальные тесты могут не иметь прав)."""
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(["chown", "root:root", str(path)], check=False, timeout=_SYSTEMCTL_TIMEOUT)


def _platform_gid() -> str:
    """Return platform group gid (empty if unknown)."""
    try:
        import grp

        return str(grp.getgrnam("platform").gr_gid)
    except KeyError:
        return ""


def _systemctl(*args: str) -> None:
    """Run systemctl (subprocess)."""
    subprocess.run(["systemctl", *args], check=False, timeout=_SYSTEMCTL_TIMEOUT)


# endregion FUNC_helpers


# region FUNC_main
def main() -> int:
    """CLI entry: `python3 installer.py` (thin facade).

    ▶ ┌env (PLATFORM_ROOT, AGE_SECRET_KEY)┐ → ○ run() → ⎋ exit 0|1
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    return 0 if run() else 1


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
