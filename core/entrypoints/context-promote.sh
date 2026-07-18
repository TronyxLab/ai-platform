#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint context-promote platform context org git-mirror push ssh fallback
# STRUCTURE: ▶ validate CONTEXT → ◇ SSH available? (ssh -T git@github.com, timeout 10s) → ├─ YES: git push --mirror git@github.com:${CONTEXT}/ai-platform.git ┤ └─ NO+TOKEN: git push --mirror https://... via GIT_ASKPASS ┤ → verify ls-remote vs rev-parse ◇ IMP:10 log → exit 0/1
# region MODULE_CONTRACT
## @purpose  Entry-point for `make context-promote`: promote platform to a context GitHub org
## @scope    Called ONLY from Makefile. Requires CONTEXT=<context>
## @invariants
##   - CONTEXT env var is required
##   - GIT_MIRROR_TOKEN env var is OPTIONAL (B4 fix: SSH push via operator's ssh-agent)
##   - Primary: git push --mirror git@github.com:<context>/ai-platform.git (SSH, ключ из ssh-agent)
##   - Fallback: HTTPS+GIT_MIRROR_TOKEN через GIT_ASKPASS (если SSH недоступен И токен задан)
##   - Fail-fast если оба канала недоступны
##   - Copies code from tronyx161/ai-platform to <org>/ai-platform via git push --mirror
##   - Target repository must already exist (created by `make new-context`)
##   - GIT_MIRROR_TOKEN передаётся через GIT_ASKPASS — временный скрипт, очистка trap EXIT
##   - Токен никогда не появляется в git URL, process list или shell history
## @rationale One of the 2 DEPLOY operator entry points (with `make deploy`).
##            Uses git push --mirror for complete ref synchronization (all branches, tags).
##            Verification via ls-remote ensures the mirror is consistent before declaring success.
##            SSH primary because context-promote runs locally at operator machine — ssh-agent has
##            the key; node secrets (GIT_MIRROR_TOKEN) are unavailable locally (B4 root cause).
## @changes   2026-07-09 · TASK-3 · Replaced instruction-stub with real git push/mirror logic
##           2026-07-18 · T3.4/B4 — SSH primary + HTTPS fallback; GIT_MIRROR_TOKEN опционален
## 🧐 TRAP[DECISION] · 2026-07-18 · — · SSH primary, HTTPS fallback
## · Rejected: HTTPS-only (required token, unavailable locally — B4)
## · Reason: context-promote runs locally at operator — ssh-agent has operator key,
##   node secrets (GIT_MIRROR_TOKEN) unavailable. SSH is zero-config for operator.
## · Rev: if CI-driven context-promote is introduced (runner without SSH key) → promote
##   HTTPS+token to primary, SSH to optional.
# endregion MODULE_CONTRACT
set -euo pipefail
_EP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_EP_DIR}/../lib/paths.sh"

# region CONTEXT_VALIDATION
## @purpose Validate CONTEXT env var — must resolve to a GitHub org name
CONTEXT="${CONTEXT:-}"
if [[ -z "$CONTEXT" ]]; then
    echo "[IMP:10][context-promote] ERROR: CONTEXT env var is required" >&2
    echo "Usage: CONTEXT=<context> $0" >&2
    exit 1
fi
# endregion CONTEXT_VALIDATION

# region SSH_AVAILABILITY_CHECK
## @purpose Check if SSH key for github.com is available in ssh-agent
## Uses `ssh -T git@github.com` with 10s timeout. Exit code 0 means SSH is usable.
SSH_AVAILABLE=0
if ssh -T -o ConnectTimeout=10 -o BatchMode=yes git@github.com 2>&1 | grep -q "successfully authenticated\|Hi.*"; then
    SSH_AVAILABLE=1
    echo "[IMP:8][context-promote] SSH key for github.com available — will use SSH primary channel"
else
    echo "[IMP:8][context-promote] SSH key not available or timeout — will attempt fallback"
fi
# endregion SSH_AVAILABILITY_CHECK

# region GIT_TOKEN_RESOLUTION
## @purpose Resolve GIT_MIRROR_TOKEN from env — required only for HTTPS fallback
GIT_MIRROR_TOKEN="${GIT_MIRROR_TOKEN:-}"
if [[ "$SSH_AVAILABLE" -eq 0 && -z "$GIT_MIRROR_TOKEN" ]]; then
    echo "[IMP:10][context-promote] FATAL: SSH unavailable AND GIT_MIRROR_TOKEN not set" >&2
    echo "Either ensure ssh-agent has a key for git@github.com, or set GIT_MIRROR_TOKEN PAT" >&2
    echo "  (1) ssh-add -L | grep github.com || ssh-add ~/.ssh/id_ed25519" >&2
    echo "  (2) Set GIT_MIRROR_TOKEN (your GitHub PAT) and CONTEXT, then: make context-promote" >&2
    exit 1
fi
# endregion GIT_TOKEN_RESOLUTION

echo "[IMP:9][context-promote] Promoting platform to context org: ${CONTEXT}"

# region SSH_PUSH
if [[ "$SSH_AVAILABLE" -eq 1 ]]; then
    SSH_TARGET="git@github.com:${CONTEXT}/ai-platform.git"
    echo "[IMP:8][context-promote] SSH target: ${SSH_TARGET}"

    if git push --mirror "${SSH_TARGET}" 2>&1; then
        echo "[IMP:9][context-promote] SSH push to ${CONTEXT}/ai-platform successful"
        MIRROR_HEAD=$(git ls-remote "${SSH_TARGET}" HEAD | cut -f1)
    else
        echo "[IMP:10][context-promote] FAILED: SSH push to ${CONTEXT}/ai-platform failed" >&2
        echo "Check that target org ${CONTEXT}/ai-platform exists and operator has push access" >&2
        echo "FATAL: create ${CONTEXT}/ai-platform first" >&2
        exit 1
    fi
else
    echo "[IMP:8][context-promote] SSH unavailable — falling back to HTTPS+token"
    # region GIT_ASKPASS_SETUP
    ## @purpose Set up GIT_ASKPASS temporary script for credential delivery
    ## Git invokes $GIT_ASKPASS when it needs credentials — the script
    ## prints the token to stdout. Token never appears in git URL, ps aux,
    ## or shell history. Cleaned up via trap on EXIT.
    GIT_ASKPASS_SCRIPT=$(mktemp /tmp/git-askpass.XXXXXX)
    cat > "$GIT_ASKPASS_SCRIPT" <<'ASKPASS_EOF'
#!/bin/sh
echo "${GIT_MIRROR_TOKEN}"
ASKPASS_EOF
    chmod +x "$GIT_ASKPASS_SCRIPT"
    export GIT_ASKPASS="$GIT_ASKPASS_SCRIPT"
    trap 'rm -f "$GIT_ASKPASS_SCRIPT"' EXIT
    echo "[IMP:8][context-promote] GIT_ASKPASS set up at ${GIT_ASKPASS_SCRIPT}"
    # endregion GIT_ASKPASS_SETUP

    TARGET_URL="https://github.com/${CONTEXT}/ai-platform.git"
    echo "[IMP:8][context-promote] HTTPS target: ${TARGET_URL}"

    # ──────────────────────────────────────────────────────────────────
    # [IMP:9][MIRROR][PUSH] Push all refs to target via git push --mirror
    # --mirror pushes all branches and tags, creating a complete replica
    # ──────────────────────────────────────────────────────────────────
    if git push --mirror "${TARGET_URL}" 2>&1; then
        echo "[IMP:9][context-promote] HTTPS push to ${CONTEXT}/ai-platform successful"
        MIRROR_HEAD=$(git ls-remote "${TARGET_URL}" HEAD | cut -f1)
    else
        echo "[IMP:10][context-promote] FAILED: HTTPS push to ${CONTEXT}/ai-platform failed" >&2
        echo "FATAL: create ${CONTEXT}/ai-platform first" >&2
        exit 1
    fi
fi
# endregion SSH_OR_HTTPS_PUSH

# region MIRROR_VERIFICATION
## @purpose Post-push HEAD verification — compare remote HEAD with local HEAD
SOURCE_HEAD=$(git rev-parse HEAD)
echo "[IMP:8][context-promote] Source HEAD: ${SOURCE_HEAD}"
echo "[IMP:8][context-promote] Mirror HEAD: ${MIRROR_HEAD}"

if [[ "${MIRROR_HEAD}" == "${SOURCE_HEAD}" ]]; then
    echo "[IMP:9][context-promote] Mirror sync verified: ${SOURCE_HEAD:0:7}"
    echo "[IMP:10][context-promote] SUCCESS: platform promoted to ${CONTEXT}/ai-platform"
    exit 0
else
    echo "[IMP:10][context-promote] FAIL: mirror HEAD (${MIRROR_HEAD:0:7}) != source HEAD (${SOURCE_HEAD:0:7})" >&2
    exit 1
fi
# endregion MIRROR_VERIFICATION
