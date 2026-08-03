#!/usr/bin/env python3
# GREP_SUMMARY: docker-auth, docker-login, ghcr-login, registry-auth, shared, password-stdin, config-json
# STRUCTURE: ▶ ┌docker_login┐ → ◇ env fallback → ⊕ subprocess --password-stdin → ⎋ bool
#            ▶ ┌ghcr_login┐ → ◇ env fallback → ⊕ subprocess ghcr.io --password-stdin → ⎋ bool
#            ▶ ┌configure_docker_auth┐ → ◇ base64 credentials → ⊕ dict {auths: {...}} → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Single Docker registry auth module that replaces 5 duplicate auth points
##           (lib/docker.sh, docker_registry_auth.py, state_machine.py._ghcr_auth,
##            steps.py._ghcr_docker_login, core/entrypoints/deploy-context.sh).
##           Consolidates Docker Hub login, ghcr.io login, and docker config.json generation
##           into one canonical shared module.
## @scope    Low-level auth operations — docker login via --password-stdin, ghcr.io login,
##           docker config.json dict generation. NOT business logic: does not know about
##           contexts, projects, deployments, or Docker daemon restart orchestration.
##           All functions return bool or dict — caller decides severity of failure.
## @invariants
##   1. All functions operate via subprocess.run(['docker', 'login', ...]) — no SDK, no shell=True
##   2. Tokens passed exclusively via stdin (--password-stdin flag), never on command line
##   3. stdout redirected to /dev/null (subprocess.DEVNULL) to prevent token leakage in logs
##   4. Non-fatal credential fallback: missing env vars → log at IMP:7, return True (anonymous)
##   5. No file I/O in configure_docker_auth — returns dict, caller decides persistence
## @rationale DRIFT elimination (D8): 5 duplicate Docker auth implementations consolidated into
##            one canonical shared module. --password-stdin + DEVNULL prevents token leak which
##            was a systemic risk in the old per-file implementations. Each had subtle differences
##            in error handling and logging — now unified with consistent IMP levels.
## @changes  2026-07-30 · — Created as shared module (DRIFT-D8)
## @usecases
##   - docker_login: bootstrap pipeline Docker Hub auth, module pull auth
##   - ghcr_login: ghcr.io image pull auth during bootstrap
##   - configure_docker_auth: registry mirror config generation for daemon.json
## @modulemap
##   ┌docker_login()┐ → subprocess.run(["docker", "login", ...], --password-stdin)
##   ┌ghcr_login()┐  → subprocess.run(["docker", "login", "ghcr.io", ...], --password-stdin)
##   ┌configure_docker_auth()┐ → base64.b64encode → dict {"auths": {url: {"auth": ...}}}
# endregion MODULE_CONTRACT

import base64
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# region FUNC_docker_login
# ═══════════════════════════════════════════════════════════════════


def docker_login(
    registry: str = "https://index.docker.io/v1/",
    username: str | None = None,
    token: str | None = None,
) -> bool:
    """Authenticate to a Docker registry via --password-stdin.

    ▶ ┌registry+username+token┐ → ◇ env fallback (DOCKER_HUB_USERNAME/TOKEN)
    │ → ◇ no creds? → IMP:7 → ⎋ True (anonymous)
    │ → ⊕ subprocess --password-stdin → ⎋ bool

    ## @purpose — Login to Docker registry with token-based auth, stdout suppressed
    ##            to prevent credential leakage in CI/logs.
    ## @io — ⇥ registry: str (default Docker Hub), username: str | None, token: str | None
    ##   → ⎋ bool (True = auth success OR anonymous fallback)
    ## @complexity — O(1) + network I/O
    ## @invariants
    ##   - Token passed via stdin (--password-stdin) — never on command line
    ##   - stdout → subprocess.DEVNULL prevents token leakage in logs/captures
    ##   - Missing credentials → log at IMP:7, return True (anonymous, non-fatal)
    ##   - docker command not found → log IMP:10, return False
    ##   - OS error (e.g. permission denied) → log IMP:10, return False
    ## @rationale Anonymous fallback is non-fatal because Docker allows anonymous pulls
    ##            with rate limits. Credential auth enables higher rate limits.
    ## @changes 2026-07-30 · — Initial implementation
    """
    # ── Resolve credentials: arg → env var → anonymous ──
    if username is None:
        username = os.environ.get("DOCKER_HUB_USERNAME", "")
    if token is None:
        token = os.environ.get("DOCKER_HUB_TOKEN", "")

    if not username or not token:
        logger.info("[IMP:7][docker_login] No credentials — anonymous login to %s", registry)
        return True  # Non-fatal: anonymous fallback, Docker allows with rate limits

    logger.info("[IMP:7][docker_login] Logging into %s as %s", registry, username)

    try:
        result = subprocess.run(
            ["docker", "login", registry, "--username", username, "--password-stdin"],
            input=token,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][docker_login] Auth success for %s (user=%s)", registry, username)
            return True
        logger.warning(
            "[IMP:7][docker_login] Auth failed (exit=%d, stderr=%.200s)",
            result.returncode,
            result.stderr.strip() or "",
        )
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][docker_login] docker command not found on PATH")
        return False
    except OSError as e:
        logger.error("[IMP:10][docker_login] OS error running docker login: %s", e)
        return False


# endregion FUNC_docker_login


# ═══════════════════════════════════════════════════════════════════
# region FUNC_ghcr_login
# ═══════════════════════════════════════════════════════════════════


def ghcr_login(token: str | None = None, user: str = "ci-deploy") -> bool:
    """Authenticate to ghcr.io (GitHub Container Registry) with a PAT.

    ▶ ┌token+user┐ → ◇ env fallback (GHCR_PULL_TOKEN) → ◇ no token? → IMP:7 → ⎋ True
    │ → ⊕ subprocess ghcr.io --password-stdin → ⎋ bool

    ## @purpose — Login to ghcr.io using a GitHub personal access token.
    ##            Required for pulling private container images during bootstrap.
    ## @io — ⇥ token: str | None (fallback: $GHCR_PULL_TOKEN), user: str (default ci-deploy)
    ##   → ⎋ bool (True = auth success OR anonymous fallback)
    ## @complexity — O(1) + network I/O
    ## @invariants
    ##   - Token fallback: os.environ["GHCR_PULL_TOKEN"] if arg is None
    ##   - Same --password-stdin + DEVNULL pattern as docker_login
    ##   - Non-fatal: missing token → returns True (anonymous pull)
    ##   - docker command not found → IMP:10, return False
    ## @rationale GHCR pull token is separate from Docker Hub credentials.
    ##            Anonymous fallback allows public image pulls without auth.
    ## @changes 2026-07-30 · — Initial implementation
    """
    if token is None:
        token = os.environ.get("GHCR_PULL_TOKEN", "")

    if not token:
        logger.info("[IMP:7][ghcr_login] No GHCR token — anonymous login to ghcr.io")
        return True  # Non-fatal: anonymous fallback

    logger.info("[IMP:7][ghcr_login] Logging into ghcr.io as %s", user)

    # ⚠️ TRAP[BUG] 2026-08-03 · creds попадали в /root/.docker (receive от ci-deploy — unauthorized)
    # · Symptom: DeployOrchestrator.receive (ci-deploy) → docker compose pull ghcr.io/...
    #   «error from registry: unauthorized» — docker login при bootstrap выполнялся от root,
    #   creds писались в /root/.docker/config.json (HOME процесса), а receive читает
    #   /home/ci-deploy/.docker/config.json.
    # · Fix: HOME=<user-home> в env subprocess (creds — в docker config пользователя receive).
    docker_env = dict(os.environ)
    user_home = f"/home/{user}"
    if user != "root" and os.path.isdir(user_home):
        docker_env["HOME"] = user_home

    try:
        result = subprocess.run(
            ["docker", "login", "ghcr.io", "--username", user, "--password-stdin"],
            input=token,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            env=docker_env,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][ghcr_login] Auth success for ghcr.io as %s", user)
            return True
        logger.warning(
            "[IMP:7][ghcr_login] Auth failed for ghcr.io (exit=%d, stderr=%.200s)",
            result.returncode,
            result.stderr.strip() or "",
        )
        return False
    except FileNotFoundError:
        logger.error("[IMP:10][ghcr_login] docker command not found on PATH")
        return False
    except OSError as e:
        logger.error("[IMP:10][ghcr_login] OS error running docker login for ghcr.io: %s", e)
        return False


# endregion FUNC_ghcr_login


# ═══════════════════════════════════════════════════════════════════
# region FUNC_configure_docker_auth
# ═══════════════════════════════════════════════════════════════════


def configure_docker_auth(
    username: str,
    token: str,
    mirror_url: str | None = None,
) -> dict:
    """Generate a ~/.docker/config.json compatible dict for a mirror registry.

    ▶ ┌username+token+mirror_url┐ → ◇ base64.encode("user:token")
    │ → ⊕ dict {auths: {url: {auth: encoded}}} → ⎋ dict

    ## @purpose — Build a Docker config.json dict with base64-encoded credentials.
    ##            Returns a plain dict — no file I/O, no side effects. The caller
    ##            decides how to use it (write to ~/.docker/config.json, merge
    ##            into existing config, pass to Docker SDK, etc.).
    ## @io — ⇥ username: str, token: str, mirror_url: str | None
    ##   → ⎋ dict: {"auths": {url: {"auth": base64("username:token")}}}
    ## @complexity — O(1)
    ## @invariants
    ##   - Returns {"auths": {url: {"auth": "base64(username:token)"}}}
    ##   - auth value is standard base64 of "username:token" colon-separated
    ##   - No file I/O, no side effects (pure function modulo logging)
    ##   - Empty username/token → still returns valid dict with empty auth
    ##   - Logs tokens length only (len(token)), never the value itself
    ## @rationale Pure dict generation allows the caller to merge/write without
    ##            hardcoded file paths. This decouples auth config from daemon.json
    ##            orchestration that belongs in the bootstrap pipeline.
    ## @changes 2026-07-30 · — Initial implementation
    """
    url = mirror_url or "https://index.docker.io/v1/"
    auth_str = f"{username}:{token}"
    encoded = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")

    config: dict = {"auths": {url: {"auth": encoded}}}

    logger.info(
        "[IMP:8][configure_docker_auth] Generated auth config for %s (user=%s, token_len=%d)",
        url,
        username,
        len(token),
    )
    return config


# endregion FUNC_configure_docker_auth


# ═══════════════════════════════════════════════════════════════════
# region CLI
# ═══════════════════════════════════════════════════════════════════


def _cli_dispatch() -> int:
    """CLI entry point: docker-login | ghcr-login.

    ▶ ┌sys.argv[1]┐ → ◇ 'docker-login' → docker_login() → ⎋ exit code
    │              → ◇ 'ghcr-login'  → ghcr_login()  → ⎋ exit code
    │              → ◇ else          → usage → ⎋ exit 1

    ## @purpose — CLI dispatch for shell delegation. Commands match
    ##            the function names: docker-login, ghcr-login.
    ## @io — ⇥ sys.argv[1] → ⎋ exit code 0/1
    ## @complexity — O(1) + function delegation
    ## @invariants
    ##   - exit code 0 = auth success OR anonymous fallback (non-fatal)
    ##   - exit code 1 = auth failure or unknown command
    """
    import sys

    if len(sys.argv) < 2:
        print("Usage: docker_auth.py {docker-login|ghcr-login}", file=sys.stderr)
        return 1

    cmd = sys.argv[1]
    if cmd == "docker-login":
        return 0 if docker_login() else 1
    if cmd == "ghcr-login":
        return 0 if ghcr_login() else 1
    print(f"Unknown command: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    sys.exit(_cli_dispatch())


# endregion CLI
