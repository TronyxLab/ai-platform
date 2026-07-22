#!/usr/bin/env python3
# GREP_SUMMARY: docker-registry-auth, docker-hub-login, registry-mirror, daemon-json, rate-limit, idempotent, systemctl-restart
# STRUCTURE: ▶ ┌username+token+mirror┐ → ○ docker login → ◇ write daemon.json → ⚡ systemctl restart docker → ⊕ bool → ⎋
# region MODULE_CONTRACT
## @purpose  Configure Docker Hub authentication and optional registry-mirror to eliminate
##           rate-limiting (HTTP 429) during bootstrap image pulls.
## @scope    Called from state_machine.py step 4.5 (docker_auth, index 5) after install_docker.
##           Writes /etc/docker/daemon.json with registry-mirrors and log-driver config.
##           Performs `docker login` to Docker Hub with provided credentials.
## @invariants
##   1. Idempotent: if daemon.json already has registry-mirrors → skip write, skip restart
##   2. Idempotent: docker login with existing valid credentials → no-op
##   3. Non-fatal: if credentials missing → WARN, continue (rate-limit may apply)
##   4. Requires Docker installed (install_docker step must have run first)
##   5. systemctl restart docker is only called if daemon.json changed
##   6. mirror.gcr.io is a public Google mirror — no auth required (TRAP[DECISION] in DevPlan)
## @rationale StatusReport 045: Docker Hub rate-limit (429) blocked nginx pull during bootstrap.
##           Configuring Docker Hub auth + registry-mirror eliminates anonymous rate-limit.
##           Registry-mirror (mirror.gcr.io) provides a pull-through cache that reduces
##           direct Docker Hub requests.
## @changes  2026-07-22 | DevPlan 047 Phase 2 — Created Docker Hub auth + registry-mirror module
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
DEFAULT_MIRROR_URL = "https://mirror.gcr.io"
DAEMON_JSON_PATH = "/etc/docker/daemon.json"
DOCKER_RESTART_TIMEOUT = 60  # seconds


# region FUNC_configure_docker_auth
## @purpose — Configure Docker Hub auth + registry-mirror in daemon.json.
##            Performs docker login if credentials provided.
##            Idempotent: skips write if daemon.json already configured.
## @io — ⇥ username: str, token: str, mirror_url: Optional[str] → ⎋ bool (True = configured/written)
## @complexity — O(1) + subprocess for docker login
## @invariants
##   - If daemon.json already has registry-mirrors with mirror_url → skip (idempotent)
##   - If docker login already valid → skip (idempotent)
##   - On write: systemctl restart docker (only if daemon.json changed)
##   - Non-fatal: missing credentials → WARN, return True (already "configured" as best-effort)
def configure_docker_auth(
    username: str,
    token: str,
    mirror_url: str | None = None,
) -> bool:
    """Configure Docker Hub auth + optional registry mirror.

    ▶ ┌username+token+mirror┐ → ○ docker login → ◇ write daemon.json → ⚡ systemctl restart → ⊕ bool → ⎋

    Returns True if configured (or already configured), False on error.
    """
    mirror = mirror_url or DEFAULT_MIRROR_URL
    logger.info("[IMP:8][docker_auth] Configuring Docker Hub auth (mirror=%s)", mirror)

    # ── Step 1: Write daemon.json with registry-mirror (idempotent) ──
    written = _write_daemon_json(mirror)
    if written:
        logger.info("[IMP:9][docker_auth] daemon.json updated — restarting Docker")
        _restart_docker()
    else:
        logger.info("[IMP:7][docker_auth] daemon.json already configured — skipping restart")

    # ── Step 2: Docker login (idempotent, non-fatal) ──
    if not username or not token:
        logger.warning("[IMP:7][docker_auth] Docker Hub credentials not set — rate-limit (429) may apply")
        return True  # Non-fatal: mirror is still configured

    login_ok = _docker_login(username, token)
    if not login_ok:
        logger.warning("[IMP:7][docker_auth] Docker Hub login failed — rate-limit may apply")
    return True


# endregion FUNC_configure_docker_auth


# region FUNC_write_daemon_json
## @purpose — Write /etc/docker/daemon.json with registry-mirrors and log config.
##            Idempotent: if existing config already contains the mirror → skip.
## @io — ⇥ mirror_url: str → ⎋ bool (True if written, False if skipped)
## @complexity — O(1)
## @invariants
##   - Preserves existing keys in daemon.json (merge, not overwrite)
##   - Only adds registry-mirrors if mirror_url not already present
##   - Adds log-driver + log-opts if not present
##   - Creates parent dir if missing
def _write_daemon_json(mirror_url: str) -> bool:
    """Write daemon.json with registry-mirror. Returns True if file was written/changed."""
    # Read existing daemon.json or start fresh
    existing: dict[str, Any] = {}
    if os.path.isfile(DAEMON_JSON_PATH):
        try:
            with open(DAEMON_JSON_PATH) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[IMP:7][docker_auth] Corrupt daemon.json, overwriting: %s", e)
            existing = {}

    # Check if mirror already present
    mirrors: list[str] = existing.get("registry-mirrors", [])
    if mirror_url in mirrors:
        logger.info("[IMP:7][docker_auth] Mirror %s already in daemon.json", mirror_url)
        # Check if log-driver is also set
        if existing.get("log-driver") == "json-file":
            return False  # Fully configured

    # Merge: add mirror if not present
    if mirror_url not in mirrors:
        mirrors.append(mirror_url)
    existing["registry-mirrors"] = mirrors

    # Add log-driver config (json-file with rotation) if not present
    if "log-driver" not in existing:
        existing["log-driver"] = "json-file"
    if "log-opts" not in existing:
        existing["log-opts"] = {"max-size": "10m", "max-file": "3"}

    # Write atomically (write to tmp then rename)
    os.makedirs(os.path.dirname(DAEMON_JSON_PATH), exist_ok=True)
    tmp_path = DAEMON_JSON_PATH + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, DAEMON_JSON_PATH)
        logger.info("[IMP:9][docker_auth] daemon.json written with registry-mirrors: %s", mirrors)
        return True
    except OSError as e:
        logger.error("[IMP:10][docker_auth] Failed to write daemon.json: %s", e)
        return False


# endregion FUNC_write_daemon_json


# region FUNC_docker_login
## @purpose — Perform `docker login` to Docker Hub with provided credentials.
##            Uses password-stdin for security (no token in command line/ps).
## @io — ⇥ username: str, token: str → ⎋ bool (True = success)
## @complexity — O(1) + subprocess
## @invariants
##   - Token passed via stdin (never in command arguments)
##   - Non-fatal: failure logs WARN and returns False
##   - Idempotent: Docker caches credentials in ~/.docker/config.json
def _docker_login(username: str, token: str) -> bool:
    """Login to Docker Hub via password-stdin. Returns True on success."""
    try:
        result = subprocess.run(
            ["bash", "-c", f"echo '{token}' | docker login -u '{username}' --password-stdin"],
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker_auth] Docker Hub login successful for user %s", username)
            return True
        logger.warning(
            "[IMP:7][docker_auth] Docker Hub login failed (exit=%d): %s",
            result.returncode,
            result.stderr.strip()[:200],
        )
        return False
    except subprocess.TimeoutExpired:
        logger.warning("[IMP:7][docker_auth] Docker Hub login timed out")
        return False
    except FileNotFoundError as e:
        logger.warning("[IMP:7][docker_auth] Docker binary not found: %s", e)
        return False


# endregion FUNC_docker_login


# region FUNC_restart_docker
## @purpose — Restart Docker daemon via systemctl after daemon.json change.
##            Waits for Docker to become responsive after restart.
## @io — ⇥ None → ⎋ bool (True = restarted and responsive)
## @complexity — O(1) + subprocess
## @invariants
##   - Uses `systemctl restart docker`
##   - Waits up to DOCKER_RESTART_TIMEOUT seconds for Docker socket
##   - Non-fatal: if restart fails, logs WARN and returns False
def _restart_docker() -> bool:
    """Restart Docker daemon. Returns True if successful."""
    try:
        result = subprocess.run(
            ["systemctl", "restart", "docker"],
            capture_output=True,
            text=True,
            timeout=DOCKER_RESTART_TIMEOUT,
        )
        if result.returncode != 0:
            logger.warning(
                "[IMP:7][docker_auth] systemctl restart docker failed (exit=%d): %s",
                result.returncode,
                result.stderr.strip()[:200],
            )
            return False
        # Wait for Docker to be responsive
        import time

        for _ in range(6):  # 6 × 5s = 30s max wait
            time.sleep(5)
            check = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if check.returncode == 0:
                logger.info("[IMP:9][docker_auth] Docker restarted and responsive")
                return True
        logger.warning("[IMP:7][docker_auth] Docker restart succeeded but daemon not responding after 30s")
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[IMP:7][docker_auth] Docker restart error: %s", e)
        return False


# endregion FUNC_restart_docker


# region CLI


# region FUNC_main_cli
## @purpose — CLI entry point for standalone testing. Reads creds from env vars.
## @io — ⇥ sys.argv → ⎋ exit code (0 = success, 1 = error)
## @complexity — O(1) + subprocess
def main() -> int:
    """CLI entry point for docker_registry_auth."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    username = os.environ.get("DOCKER_HUB_USERNAME", "")
    token = os.environ.get("DOCKER_HUB_TOKEN", "")
    if not username or not token:
        logger.warning("[IMP:7][docker_auth] DOCKER_HUB_USERNAME/DOCKER_HUB_TOKEN not set")
    ok = configure_docker_auth(username, token)
    return 0 if ok else 1


# endregion FUNC_main_cli


# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
