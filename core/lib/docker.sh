#!/usr/bin/env bash
# GREP_SUMMARY: docker login library ghcr registry-credentials container-registry ghcr-login ghcr_login GHCR_PULL_TOKEN docker_auth
# STRUCTURE: ┌delegate to docker_auth.py┐ → ◇ docker_login (thin facade) → ◇ ghcr_login (thin facade) → ⎋ exit
# ═══════════════════════════════════════════════════════════════════
# MODULE_CONTRACT — Canonical Docker Login Library
# ═══════════════════════════════════════════════════════════════════
# region MODULE_CONTRACT
## @modulecontract
## @purpose  Canonical Docker and GHCR authentication library (thin facades).
##           Delegates docker_login() and ghcr_login() to the shared
##           docker_auth.py module. All credential handling lives in the shared module.
##           ensure_docker_network УДАЛЁН (волна 118 B6 — 0 callers; первичный
##           сетевой провайдер — provision-environment.sh, TRAP[DECISION] снят).
## @scope    — docker_login() thin facade → shared docker_auth.docker_login()
##           — ghcr_login() thin facade → shared docker_auth.ghcr_login()
##           — logs at IMP:8 for delegation traceability
## @input    — DOCKER_HUB_USERNAME (env var, optional)
##           — DOCKER_HUB_TOKEN    (env var, optional)
##           — GHCR_PULL_TOKEN     (env var, optional)
## @output   — Authenticated Docker Hub + GHCR sessions (side effect)
##           — Structured stderr logs via log_imp
## @links    — USED_BY: orchestrator_cli.py / deploy_engine.py,
##             core/internal/bootstrap/deploy-modules.sh
##           — REPLACES: inline docker_login() in the legacy deploy shell,
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
##            the legacy deploy shell and deploy-modules.sh
##           MODIFIED: 2026-07-17 · T13 — Added ghcr_login() from deploy-modules.sh
##           MODIFIED: 2026-07-30 · T13a — Delegated to shared docker_auth module
## @modulemap — docker_login  [W:100] Canonical Docker Hub authentication
##             — ghcr_login   [W:100] Canonical GHCR authentication
## @usecases  — CI/CD deploy: DOCKER_HUB_USERNAME + TOKEN set → login
##             — CI/CD deploy: GHCR_PULL_TOKEN set → ghcr.io login
##             — Anonymous bootstrap: env vars absent → log warning, continue
# endregion MODULE_CONTRACT
# GREP_SUMMARY: docker, docker-login, docker_login, DOCKER_HUB_USERNAME, DOCKER_HUB_TOKEN, auth, registry, docker_auth
# STRUCTURE: ▶ ┌delegate to docker_auth.py┐ → ◇ docker-login (thin facade) → ⎋ exit
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
    # ⚠️ TRAP[DECISION] · 2026-07-30 · — · Delegated to shared docker_auth.py
    # · Rejected: inline subprocess.run --password-stdin (duplicate auth logic)
    # · Reason: DRIFT-D8 consolidation — 5 duplicate auth sites → 1 canonical module
    # · Rev: if shared module interface changes, update thin facade
    log_imp 8 "docker_login" "Delegating to docker_auth.py (shared module)"
    python3 "${BASH_SOURCE[0]%/*}/../internal/shared/docker_auth.py" docker-login
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
##           Extracted from deploy-modules.sh to lib/ so the deploy pipeline can also
##           call ghcr_login() if needed, without duplicating the function.
ghcr_login() {
    # ⚠️ TRAP[DECISION] · 2026-07-30 · — · Delegated to shared docker_auth.py
    # · Rejected: inline subprocess.run --password-stdin (duplicate auth logic)
    # · Reason: DRIFT-D8 consolidation — 5 duplicate auth sites → 1 canonical module
    # · Rev: if shared module interface changes, update thin facade
    log_imp 8 "ghcr_login" "Delegating to docker_auth.py (shared module)"
    python3 "${BASH_SOURCE[0]%/*}/../internal/shared/docker_auth.py" ghcr-login
}
# endregion FUNC_ghcr_login
