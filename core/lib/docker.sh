#!/usr/bin/env bash
# GREP_SUMMARY: docker login library ghcr registry-credentials container-registry ghcr-login ghcr_login GHCR_PULL_TOKEN
# STRUCTURE: ┌env vars (DOCKER_HUB_USERNAME+TOKEN | GHCR_PULL_TOKEN)┐ → ◇ docker_login → ◇ ghcr_login → ◇ fallback anonymous → ⊕ exit
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Canonical Docker Login Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Canonical Docker and GHCR authentication library.
##           Provides docker_login() for Docker Hub and ghcr_login() for
##           GitHub Container Registry, with graceful anonymous fallback.
##           Eliminates duplicate login definitions across deploy scripts.
## @scope    — docker_login() with env-var-based auth (Docker Hub)
##           — ghcr_login() with GHCR_PULL_TOKEN auth (ghcr.io)
##           — logs at IMP:7-9 for traceability
##           — zero side-effects on source (pure function definition only)
## @input    — DOCKER_HUB_USERNAME (env var, optional)
##           — DOCKER_HUB_TOKEN    (env var, optional)
##           — GHCR_PULL_TOKEN     (env var, optional)
## @output   — Authenticated Docker Hub + GHCR sessions (side effect)
##           — Structured stderr logs via log_imp
## @links    — USED_BY: core/internal/deploy/deploy-project.sh,
##             core/internal/bootstrap/deploy-modules.sh
##           — REPLACES: inline docker_login() in deploy-project.sh:82,
##             deploy-modules.sh:35; inline ghcr_login() in deploy-modules.sh:65
## @invariants — Self-contained: sources logging.sh internally; no caller
##               dependency on logging.sh.
##             — Anonymous fallback MUST NOT exit non-zero — missing
##               credentials are a soft warning, not a failure.
##             — docker login output is redirected to /dev/null (2>&1)
##               to avoid leaking tokens in CI logs.
## @rationale Q: Why positive check ([[ -n ... ]]) instead of negative
##               ([[ -z ... ]])?
##            A: Positive check makes the success path the first branch,
##            keeping the happy-path logic visible and the fallback
##            secondary. Both existing implementations used [[ -z ... ]]
##            with early return, but the canonical form inlines the
##            positive guard for readability.
## @changes   CREATED: 2026-07-09 · TASK-9 — Extracted from
##            deploy-project.sh and deploy-modules.sh
##           MODIFIED: 2026-07-17 · T13 — Added ghcr_login() from deploy-modules.sh
## @modulemap — docker_login  [W:100] Canonical Docker Hub authentication
##             — ghcr_login   [W:100] Canonical GHCR authentication
## @usecases  — CI/CD deploy: DOCKER_HUB_USERNAME + TOKEN set → login
##             — CI/CD deploy: GHCR_PULL_TOKEN set → ghcr.io login
##             — Anonymous bootstrap: env vars absent → log warning, continue
# endregion MODULE_CONTRACT
# GREP_SUMMARY: docker, docker-login, docker_login, DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN, auth, registry
# STRUCTURE: ▶ ┌DOCKER_HUB_USERNAME + TOKEN?┐ → ◇ ┌both set?┐ → ⚡ echo TOKEN | docker login --username USER --password-stdin → [IMP:9] success | ⚡ [IMP:8] WARN: anonymous → ⎋ return 0
__LOG_PREFIX="${__LOG_PREFIX:-docker}"
source "${BASH_SOURCE[0]%/*}/logging.sh"

# ═══════════════════════════════════════════════════════════════════
# region FUNC_docker_login
## @purpose  Authenticate to Docker Hub or continue anonymously.
##           Requires DOCKER_HUB_USERNAME and DOCKER_HUB_TOKEN env vars.
##           If both are absent → warning log, anonymous access.
##           If both are set → authenticated docker login, silent on success.
##           If login fails → warning log, continue anonymously (non-fatal).
## @param    (none — reads DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN env)
## @io       env read: DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN
##           side-effect: docker login session
##           out: stderr → [IMP:8-9] login status messages
## @complexity O(1) — one docker invocation at most
## @invariants — Never exits non-zero (anonymous fallback always succeeds)
##             — Never writes to stdout (all output via stderr)
##             - stdout from docker login (Login Succeeded) redirected to /dev/null
docker_login() {
    if [[ -n "$DOCKER_HUB_USERNAME" && -n "$DOCKER_HUB_TOKEN" ]]; then
        # Happy path: credentials present → attempt authenticated login
        log_imp 8 "docker_login" "Authenticating to Docker Hub as ${DOCKER_HUB_USERNAME}"
        echo "$DOCKER_HUB_TOKEN" | docker login --username "$DOCKER_HUB_USERNAME" --password-stdin 2>/dev/null && {
            log_imp 9 "docker_login" "Docker Hub login succeeded as ${DOCKER_HUB_USERNAME}"
            return 0
        } || {
            log_imp 9 "docker_login" "WARNING: docker login failed — continuing with anonymous access"
            return 0
        }
    else
        # Fallback: credentials absent → anonymous access
        log_imp 8 "docker_login" "DOCKER_HUB_USERNAME/TOKEN not set — continuing with anonymous access"
    fi
}
# endregion FUNC_docker_login

# ═══════════════════════════════════════════════════════════════════
# region FUNC_ghcr_login
## @purpose  Authenticate to GitHub Container Registry (ghcr.io) for image pulls.
##           Uses GHCR_PULL_TOKEN env var; falls back to anonymous access if absent.
##           Non-fatal — missing or invalid credentials produce warnings, not errors.
## @param    (none — reads GHCR_PULL_TOKEN env)
## @io       env read: GHCR_PULL_TOKEN
##           side-effect: docker login session to ghcr.io
##           out: stderr → [IMP:8-9] login status messages
## @complexity O(1) — one docker invocation at most
## @invariants — Never exits non-zero (anonymous fallback always succeeds)
##             — Never writes to stdout (all output via stderr)
##             - stdout from docker login redirected to /dev/null to avoid token leak
## @rationale GHCR is used for Hermes-built images (L1→L2 pipeline). Authentication is
##           required for private packages; anonymous access works for public images.
##           Extracted from deploy-modules.sh to lib/ so deploy-project.sh can also
##           call ghcr_login() if needed, without duplicating the function.
ghcr_login() {
    if [[ -z "${GHCR_PULL_TOKEN:-}" ]]; then
        log_imp 8 "ghcr_login" "GHCR_PULL_TOKEN not set — skipping ghcr.io login (anonymous)"
        return 0
    fi

    log_imp 8 "ghcr_login" "Authenticating to ghcr.io as root"
    local login_output
    login_output="$(echo "${GHCR_PULL_TOKEN}" | docker login ghcr.io -u x-access-token --password-stdin 2>&1)" || {
        log_imp 9 "ghcr_login" "WARNING: ghcr.io login failed — continuing with anonymous access"
        log_imp 7 "ghcr_login" "Output: ${login_output}"
        return 0
    }

    log_imp 9 "ghcr_login" "GHCR login succeeded as root"
}
# endregion FUNC_ghcr_login
