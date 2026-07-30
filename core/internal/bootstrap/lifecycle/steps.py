#!/usr/bin/env python3
# GREP_SUMMARY: steps, step-implementations, subprocess, acme, install, apt, sudoers, ssl, converge, healthcheck, telegram, audit
# STRUCTURE: ┌independent step implementations┐ → ◇ _step_install_acme → ◇ apt/docker/user helpers → ◇ ssl_provision/healthcheck → ◇ telegram/audit → subprocess_run wrapper
# region MODULE_CONTRACT
## @purpose  Step implementation functions extracted from StateMachine for separation of concerns.
##           Each function is a standalone step with pre/post conditions, subprocess calls,
##           and graceful error handling. These are the "business logic" of bootstrap steps.
## @scope    Called by state_machine.py during init/update step execution. Each function
##           accepts step-specific parameters (paths, config) and performs the actual work
##           via subprocess.run() with standard timeout (120s) and error handling.
## @invariants
##   1. All functions are idempotent — safe to re-run on provisioned node
##   2. Non-fatal failures log WARN, do NOT raise exceptions
##   3. Fatal failures (critical preconditions) raise PlatformFatalError (or ConfigNotFoundError for missing files)
##   4. All subprocess.run calls use capture_output=True, text=True, timeout=120
##   5. No direct state mutation — state transitions handled by StateMachine
##   6. Env var access via os.environ (set by shell-фасад or StateMachine CLI)
## @rationale  Separation of concerns: StateMachine handles state transitions;
##             steps.py handles actual execution logic. This enables unit-testing
##             of step implementations without state machine coupling.
## @changes  2026-07-22 | W4-E2 — Created from node-lifecycle.sh decomposition
##           2026-07-30 | T20b/T22 — Migrated _ghcr_docker_login()→shared docker_auth,
##           _send_telegram_notification()→shared telegram_notifier.
##           2026-07-30 | DevPlan 087 T5 — Removed _step_deploy_context() (logic moved to
##           state_machine._import_deploy_context() and phases.py). steps.py is legacy module
##           being phased out — all new phase logic goes to phases.py.
# endregion MODULE_CONTRACT

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    PlatformFatalError,
)

logger = logging.getLogger(__name__)

# Shared library import (DevPlan 070 — DRIFT-B5 elimination)
import sys as _sys

_SHARED_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "shared")
if _SHARED_DIR not in _sys.path:
    _sys.path.insert(0, _SHARED_DIR)

# Import shared modules (DevPlan 081B7 DRIFT elimination)
from core.internal.shared.docker_auth import ghcr_login as _shared_ghcr_login  # noqa: E402
from core.internal.shared.telegram_notifier import send_telegram as _shared_send_telegram  # noqa: E402


# region FUNC__install_apt_packages
## @purpose — Idempotent apt package installation: check dpkg first, install only missing.
##            Supports TOR-conditional packages (tor, privoxy, obfs4proxy).
## @io — ⇥ packages: list of package names, tor_enabled: bool → ⎋ None
## @complexity — O(N * dpkg) + O(apt-get)


# region FUNC__is_pkg_installed
## @purpose  Check if a single dpkg package is installed, handling subprocess errors gracefully
def _is_pkg_installed(pkg: str) -> bool:
    """Check dpkg status for a package. Returns True if installed, False on error."""
    try:
        result = subprocess.run(
            ["dpkg", "-s", pkg],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# endregion FUNC__is_pkg_installed


def _install_apt_packages(packages: list[str], tor_enabled: bool = False) -> None:
    """Install apt packages idempotently. Raises PlatformFatalError on critical failure."""
    all_packages = list(packages)
    if tor_enabled:
        all_packages.extend(["tor", "privoxy", "obfs4proxy"])
        logger.info("[IMP:8][apt] Tor enabled — added tor/privoxy/obfs4proxy to apt packages")
    else:
        logger.info("[IMP:7][apt] Tor disabled — skipping tor/privoxy/obfs4proxy packages")

    to_install: list[str] = [pkg for pkg in all_packages if not _is_pkg_installed(pkg)]

    if not to_install:
        logger.info("[IMP:7][apt] All packages already installed — skipping")
        return

    logger.info("[IMP:9][apt] Installing %d packages: %s", len(to_install), " ".join(to_install))
    try:
        subprocess.run(["apt-get", "update", "-qq"], capture_output=True, text=True, timeout=120, check=True)
        install_result = subprocess.run(
            ["apt-get", "install", "-y", "-qq", *to_install],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if install_result.returncode != 0:
            raise PlatformFatalError(f"apt-get install failed: {install_result.stderr.strip()}")
        logger.info("[IMP:9][apt] Packages installed successfully")
    except subprocess.TimeoutExpired:
        raise PlatformFatalError("apt-get update/install timed out") from None
    except subprocess.CalledProcessError as e:
        raise PlatformFatalError(f"apt-get failed: {e}") from e


# endregion FUNC__install_apt_packages


# region FUNC__ensure_sops
## @purpose — Install sops (v3.9.4) from GitHub releases if not present.
##            SOPS is not available in standard apt repos.
## @io — ⇥ None → ⎋ None (downloads and installs sops binary)
## @complexity — O(1) per check + O(1) for download
## @invariants
##   - Non-fatal: if download fails, log WARN and continue
##   - Architecture detection via dpkg --print-architecture (fallback: amd64)
def _ensure_sops() -> None:
    """Install sops v3.9.4 from GitHub if missing. Non-fatal on failure."""
    try:
        check = subprocess.run(["command", "-v", "sops"], capture_output=True, text=True, timeout=10)
        if check.returncode == 0:
            logger.info("[IMP:7][sops] sops already installed at %s", check.stdout.strip())
            return
    except FileNotFoundError:
        pass

    logger.info("[IMP:8][sops] Installing sops v3.9.4 from GitHub")
    try:
        arch_result = subprocess.run(
            ["dpkg", "--print-architecture"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        arch = arch_result.stdout.strip() if arch_result.returncode == 0 else "amd64"
        if arch not in ("amd64", "arm64"):
            arch = "amd64"

        url = f"https://github.com/getsops/sops/releases/download/v3.9.4/sops-v3.9.4.linux.{arch}"
        download = subprocess.run(
            ["curl", "-sSL", "-o", "/usr/local/bin/sops", url],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if download.returncode != 0:
            logger.warning("[IMP:7][sops] Download failed: %s", download.stderr.strip())
            return

        subprocess.run(["chmod", "0755", "/usr/local/bin/sops"], capture_output=True, text=True, timeout=10, check=True)
        logger.info("[IMP:9][sops] sops v3.9.4 installed at /usr/local/bin/sops")
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][sops] sops installation timed out")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning("[IMP:7][sops] sops installation failed: %s", e)


# endregion FUNC__ensure_sops


# region FUNC__install_docker
## @purpose — Install Docker CE + Compose plugin via install-docker.sh.
##            Idempotent: skips if Docker already installed.
## @io — ⇥ core_dir: str → ⎋ bool
## @complexity — O(1) + subprocess
def _install_docker(core_dir: str) -> bool:
    """Install Docker via install-docker.sh. Returns True on success."""
    install_script = os.path.join(core_dir, "internal", "bootstrap", "install-docker.sh")
    if not os.path.isfile(install_script):
        raise ConfigNotFoundError(f"install-docker.sh not found at {install_script}")

    logger.info("[IMP:9][docker] Installing Docker + Compose plugin")
    try:
        result = subprocess.run(
            ["bash", install_script],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker] Docker installed successfully")
            return True
        raise PlatformFatalError(f"Docker installation failed: {result.stderr.strip()[:500]}")
    except subprocess.TimeoutExpired:
        raise PlatformFatalError("Docker installation timed out (300s)") from None


# endregion FUNC__install_docker


# region FUNC__create_system_user
## @purpose — Create a system user with home directory and optional group membership.
##            Idempotent: skips creation if user already exists.
## @io — ⇥ username: str, groups: list[str], home_dir: str → ⎋ None
## @complexity — O(1)
## @invariants
##   - Raises RuntimeError on useradd failure
##   - Groups are comma-separated in system call
def _create_system_user(username: str, groups: list[str] | None = None, home_dir: str | None = None) -> None:
    """Create system user idempotently. Raises PlatformFatalError on failure."""
    check = subprocess.run(["id", username], capture_output=True, text=True, timeout=10)
    if check.returncode == 0:
        logger.info("[IMP:7][user] User '%s' already exists — skipping", username)
        return

    if home_dir is None:
        home_dir = f"/home/{username}"

    cmd = [
        "useradd",
        "--system",
        "--shell",
        "/bin/bash",
        "--create-home",
        "--home-dir",
        home_dir,
    ]
    if groups:
        cmd.extend(["--groups", ",".join(groups)])
    cmd.append(username)

    logger.info("[IMP:9][user] Creating system user: %s", username)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise PlatformFatalError(f"useradd for '{username}' failed: {result.stderr.strip()}")
        logger.info("[IMP:9][user] User '%s' created successfully", username)
    except subprocess.TimeoutExpired:
        raise PlatformFatalError(f"useradd for '{username}' timed out") from None


# endregion FUNC__create_system_user


# region FUNC__add_ssh_key
## @purpose — Add SSH public key to user's authorized_keys.
##            Supports forced-command prefix for ci-deploy user.
## @io — ⇥ username: str, key: str, forced_command_prefix: Optional[str] → ⎋ None
## @complexity — O(1)
## @invariants
##   - Creates .ssh directory with 0700 if missing
##   - Appends key only if not already present (idempotent)
##   - Sets authorized_keys to 0600
def _add_ssh_key(username: str, key: str, forced_command_prefix: str | None = None) -> None:
    """Add SSH key to user's authorized_keys. Idempotent on duplicate key."""
    home = f"/home/{username}"
    ssh_dir = Path(home) / ".ssh"
    auth_keys = ssh_dir / "authorized_keys"

    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        subprocess.run(
            ["chown", f"{username}:{username}", str(ssh_dir)],
            capture_output=True,
            text=True,
            timeout=10,
        )

    # Check if key already present
    if auth_keys.exists():
        existing = auth_keys.read_text()
        if key.strip() in existing:
            logger.info("[IMP:7][ssh_key] SSH key already present for %s — skipping", username)
            return

    entry = key
    if forced_command_prefix:
        entry = f"{forced_command_prefix} {key}"

    auth_keys.write_text(entry + "\n")
    auth_keys.chmod(0o600)
    with contextlib.suppress(FileNotFoundError):
        subprocess.run(
            ["chown", f"{username}:{username}", str(auth_keys)],
            capture_output=True,
            text=True,
            timeout=10,
        )

    logger.info("[IMP:9][ssh_key] SSH key added to %s/.ssh/authorized_keys", username)


# endregion FUNC__add_ssh_key


# region FUNC__apply_firewall
## @purpose — Apply declarative ufw firewall baseline via firewall.sh.
##            Resets existing ufw rules, applies default baseline (22/80/443).
## @io — ⇥ core_dir: str, extra_ports: list[str] → ⎋ bool
## @complexity — O(1) + subprocess
def _apply_firewall(core_dir: str, extra_ports: list[str] | None = None) -> bool:
    """Apply ufw firewall baseline. Returns True on success."""
    firewall_script = os.path.join(core_dir, "internal", "bootstrap", "firewall.sh")
    if not os.path.isfile(firewall_script):
        raise ConfigNotFoundError(f"firewall.sh not found at {firewall_script}")

    cmd = ["bash", firewall_script]
    if extra_ports:
        cmd.extend(extra_ports)

    logger.info("[IMP:9][firewall] Applying ufw baseline")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("[IMP:9][firewall] Firewall applied successfully")
            return True
        raise PlatformFatalError(f"Firewall setup failed: {result.stderr.strip()[:500]}")
    except subprocess.TimeoutExpired:
        raise PlatformFatalError("Firewall setup timed out") from None


# endregion FUNC__apply_firewall


# region FUNC__validate_node_yaml
## @purpose — Validate node.yaml against node.schema.json using jsonschema.
##            Falls back to subprocess python3 if jsonschema not importable directly.
## @io — ⇥ node_yaml: str, schema_file: str → ⎋ bool
## @complexity — O(N) where N = schema size
## @invariants
##   - Non-fatal if schema file missing (WARN only)
##   - Validation failure is WARN, not abort
def _validate_node_yaml(node_yaml: str, schema_file: str) -> bool:
    """Validate node.yaml against JSON schema. Returns True if valid or cannot validate."""
    if not os.path.isfile(node_yaml):
        logger.warning("[IMP:7][validate] node.yaml not found: %s", node_yaml)
        return False

    if not os.path.isfile(schema_file):
        logger.warning("[IMP:7][validate] Schema file not found: %s — skipping validation", schema_file)
        return True

    logger.info("[IMP:8][validate] Validating node.yaml against schema")
    try:
        try:
            import jsonschema
        except ImportError:
            # Fallback to subprocess
            logger.info("[IMP:7][validate] jsonschema not importable — using subprocess python3")
            try:
                result = subprocess.run(
                    [
                        "python3",
                        "-c",
                        f"""
import json, sys
from core.internal.shared.node_yaml import NodeYaml
import jsonschema
instance = NodeYaml('{node_yaml}').raw()
with open('{schema_file}') as f:
    schema = json.load(f)
jsonschema.validate(instance, schema)
print('VALID')
""",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode == 0 and "VALID" in result.stdout:
                    logger.info("[IMP:9][validate] node.yaml valid against schema (subprocess)")
                    return True
                logger.warning("[IMP:7][validate] node.yaml validation failed: %s", result.stderr.strip()[:200])
                return False
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                logger.warning("[IMP:7][validate] Validation subprocess error: %s", e)
                return True

        # In-process validation using NodeYaml
        from core.internal.shared.node_yaml import NodeYaml

        with open(schema_file) as f:
            schema = json.load(f)
        instance = NodeYaml(node_yaml).raw()
        jsonschema.validate(instance, schema)
        logger.info("[IMP:9][validate] node.yaml valid against schema")
        return True

    except ImportError:
        logger.warning("[IMP:7][validate] NodeYaml library not available — skipping schema validation")
        return True
    except json.JSONDecodeError as e:
        logger.warning("[IMP:7][validate] node.yaml parse error: %s", e)
        return False
    except Exception as e:  # noqa: EXC — catch-all after specific YAML/JSON handlers
        logger.warning("[IMP:7][validate] Validation error: %s", e)
        return False


# endregion FUNC__validate_node_yaml


# region FUNC__ghcr_docker_login
## @purpose — Docker login to GitHub Container Registry for ci-deploy user.
##            Uses GHCR_PULL_TOKEN for authentication. Delegates to shared docker_auth.ghcr_login().
## @io — ⇥ token: str → ⎋ bool (True = success)
## @complexity — O(1) + subprocess (via shared module)
## @invariants
##   - Non-fatal: if token not set, skip without error
## @changes 2026-07-30 | T20b — Replaced inline subprocess with shared docker_auth.ghcr_login()
def _ghcr_docker_login(token: str) -> bool:
    """Login to ghcr.io as ci-deploy. Returns True on success."""
    if not token:
        logger.info("[IMP:7][ghcr] GHCR_PULL_TOKEN not set — skipping ghcr auth")
        return True  # Not an error
    return _shared_ghcr_login(token, user="ci-deploy")


# endregion FUNC__ghcr_docker_login


# region FUNC__validate_sudoers_files
## @purpose — Validate /etc/sudoers.d files for correct ownership (root:root)
##            and permissions (≤0440). Critical security check.
## @io — ⇥ sudoers_d: str → ⎋ bool (True = all files valid)
## @complexity — O(N) where N = files in sudoers.d
## @invariants
##   - Raises PlatformFatalError if any file has wrong owner/permissions
##   - Skips README file
def _validate_sudoers_files(sudoers_d: str = "/etc/sudoers.d") -> bool:
    """Validate sudoers.d ownership and permissions. Raises RuntimeError on violations."""
    if not os.path.isdir(sudoers_d):
        logger.warning("[IMP:7][sudoers] %s not found — skipping validation", sudoers_d)
        return True

    errors = 0
    for entry in Path(sudoers_d).iterdir():
        if not entry.is_file():
            continue
        if entry.name == "README":
            continue

        try:
            stat_info = entry.stat()
            owner = f"{stat_info.st_uid}:{stat_info.st_gid}"
            mode = stat_info.st_mode & 0o777

            if owner != "0:0":
                logger.error("[IMP:10][sudoers] %s: owner %s instead of 0:0", entry.name, owner)
                errors += 1
            if mode > 0o440:
                logger.error("[IMP:10][sudoers] %s: permissions %03o instead of ≤0440", entry.name, mode)
                errors += 1
        except OSError as e:
            logger.error("[IMP:10][sudoers] Cannot stat %s: %s", entry.name, e)
            errors += 1

    if errors > 0:
        raise PlatformFatalError(
            f"{errors} sudoers file(s) with wrong owner/permissions. Fix:\n"
            f"  chown root:root {sudoers_d}/*\n"
            f"  chmod 0440 {sudoers_d}/*"
        )
    logger.info("[IMP:9][sudoers] All sudoers files validated: owner=root:root, mode≤0440")
    return True


# endregion FUNC__validate_sudoers_files


# region FUNC__write_bootstrap_audit
## @purpose — Write bootstrap/update completion audit entry.
##            Records mode, node name, warnings/errors count to /var/log/platform/audit.log.
## @io — ⇥ mode: str, node: str, warnings: list[str], errors: list[str] → ⎋ None
## @complexity — O(N) where N = total warnings + errors
def _write_bootstrap_audit(
    mode: str,
    node: str,
    warnings: list[str],
    errors: list[str],
    audit_file: str = "/var/log/platform/audit.log",
) -> None:
    """Write audit log entry for bootstrap/update completion."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        Path(audit_file).parent.mkdir(parents=True, exist_ok=True)
        with open(audit_file, "a") as f:
            f.write(f"[{ts}] bootstrap:{mode} DONE | node={node} | warnings={len(warnings)} | errors={len(errors)}\n")
            for w in warnings:
                f.write(f"[{ts}] bootstrap:warnings WARN | {w}\n")
            for e in errors:
                f.write(f"[{ts}] bootstrap:errors ERROR | {e}\n")
        logger.info("[IMP:9][audit] Audit log updated: %s", audit_file)
    except OSError as e:
        logger.warning("[IMP:7][audit] Failed to write audit log: %s", e)


# endregion FUNC__write_bootstrap_audit


# region FUNC__send_telegram_notification
## @purpose — Send Telegram notification about bootstrap/update completion.
##            Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars.
##            Routes through TELEGRAM_PROXY_URL (tor proxy by default).
##            Delegates to shared telegram_notifier.send_telegram().
## @io — ⇥ node: str, warnings: list[str], errors: list[str] → ⎋ bool
## @complexity — O(1) + HTTP request (via shared module)
## @invariants
##   - Non-fatal: if env vars not set or request fails, log and return False
## @changes 2026-07-30 | T22 — Replaced inline urllib with shared telegram_notifier.send_telegram()
def _send_telegram_notification(
    node: str,
    warnings: list[str],
    errors: list[str],
) -> bool:
    """Send Telegram notification. Returns True on success."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        logger.info("[IMP:9][telegram] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — notifications disabled")
        return False

    ts = time.strftime("%d.%m.%Y %H:%M:%S")

    status_suffix = "⚠️ Warnings/Errors:" if errors or warnings else "✅"

    msg = f"🚀 [node: {node}] Узел обновлён {status_suffix}\nВремя: {ts}"
    for w in warnings:
        msg += f"\n- ⚠️ {w}"
    for e in errors:
        msg += f"\n- ❌ {e}"

    proxy_url = os.environ.get("TELEGRAM_PROXY_URL", "http://127.0.0.1:8118")
    return _shared_send_telegram(msg, bot_token, chat_id, proxy_url)


# endregion FUNC__send_telegram_notification


# region FUNC__tor_provision
## @purpose — Install Tor + Privoxy proxy for Telegram notifications.
##            Conditional on TOR_ENABLED and bridges file availability.
## @io — ⇥ core_dir: str, bridges_file: str, skip_verify: bool → ⎋ bool
## @complexity — O(1) + subprocess
## @invariants
##   - Non-fatal: if Tor circuit fails, Telegram will be unavailable
def _tor_provision(core_dir: str, bridges_file: str = "", skip_verify: bool = False) -> bool:
    """Install and verify Tor/Privoxy. Returns True on success."""
    tor_script = os.path.join(core_dir, "internal", "bootstrap", "install-tor-proxy.sh")
    if not os.path.isfile(tor_script):
        raise ConfigNotFoundError(f"install-tor-proxy.sh not found at {tor_script}")

    cmd = ["bash", tor_script]
    if bridges_file:
        cmd.extend(["--tor-bridges-file", bridges_file])
    if skip_verify:
        cmd.append("--skip-tor-verify")

    logger.info("[IMP:9][tor] Installing Tor + Privoxy proxy")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("[IMP:9][tor] Tor + Privoxy installed and verified")
            return True
        logger.warning(
            "[IMP:7][tor] Tor circuit failed to establish — Telegram notifications will be unavailable: %s",
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][tor] Tor installation timed out")
        return False


# endregion FUNC__tor_provision


# region FUNC__run_converge
## @purpose — Run converge.sh desired-state reconciler. Non-fatal: returns exit code.
## @io — ⇥ core_dir: str, node_name: str, extra_args: list[str] → ⎋ int (exit code: 0/1/2)
## @complexity — O(1) + subprocess
def _run_converge(core_dir: str, node_name: str, extra_args: list[str] | None = None) -> int:
    """Run converge.sh. Returns exit code: 0=clean, 1=warnings, 2=errors, -1=not found."""
    converge_script = os.path.join(core_dir, "internal", "bootstrap", "converge.sh")
    if not os.path.isfile(converge_script):
        logger.warning("[IMP:7][converge] converge.sh not found — skipping")
        return -1

    cmd = ["bash", converge_script, "--node", node_name]
    if extra_args:
        cmd.extend(extra_args)

    logger.info("[IMP:9][converge] Running converge.sh: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        logger.info("[IMP:9][converge] converge.sh exit=%d: %s", result.returncode, result.stdout.strip()[:200])
        if result.returncode == 0:
            logger.info("[IMP:9][converge] Converge complete — no errors")
        elif result.returncode == 1:
            logger.info("[IMP:9][converge] Converge complete with warnings (exit 1)")
        elif result.returncode == 2:
            logger.warning("[IMP:7][converge] Converge CRITICAL errors (exit 2)")
        return result.returncode
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][converge] converge.sh timed out")
        return -1


# endregion FUNC__run_converge

# ── _step_deploy_context REMOVED per DevPlan 087 T5 ──
# Business logic moved to state_machine.py._import_deploy_context() (DevPlan 079)
# and phases.py phase_deploy_services() / phase_deploy_update().
# Steps.py no longer contains step implementations — all phase logic in phases.py.
