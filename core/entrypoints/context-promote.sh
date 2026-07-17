#!/usr/bin/env bash
# GREP_SUMMARY: entrypoint context-promote platform context org git-mirror push
# STRUCTURE: ▶ validate CONTEXT → resolve GIT_MIRROR_TOKEN → construct target_url → git push --mirror → verify ls-remote vs rev-parse ◇ IMP:10 log → exit 0/1
# region MODULE_CONTRACT
## @purpose  Entry-point for `make context-promote`: promote platform to a context GitHub org
## @scope    Called ONLY from Makefile. Requires CONTEXT=<context>
## @invariants
##   - CONTEXT env var is required
##   - GIT_MIRROR_TOKEN env var is required (fail-fast)
##   - Copies code from tronyx161/ai-platform to <org>/ai-platform via git push --mirror
##   - Target repository must already exist (created by `make new-context`)
##   - GIT_MIRROR_TOKEN передаётся через GIT_ASKPASS — временный скрипт, очистка trap EXIT
##   - Токен никогда не появляется в git URL, process list или shell history
## @rationale One of the 2 DEPLOY operator entry points (with `make deploy`).
##            Uses git push --mirror for complete ref synchronization (all branches, tags).
##            Verification via ls-remote ensures the mirror is consistent before declaring success.
## @changes   2026-07-09 · TASK-3 · Replaced instruction-stub with real git push/mirror logic
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

# region GIT_MIRROR_TOKEN_RESOLUTION
## @purpose Resolve GIT_MIRROR_TOKEN from env — fail-fast if not set
GIT_MIRROR_TOKEN="${GIT_MIRROR_TOKEN:-}"
if [[ -z "$GIT_MIRROR_TOKEN" ]]; then
    echo "[IMP:10][context-promote] ERROR: GIT_MIRROR_TOKEN env var is not set" >&2
    echo "Set GIT_MIRROR_TOKEN to a GitHub PAT with Contents:Write on ${CONTEXT}/ai-platform" >&2
    exit 1
fi
# endregion GIT_MIRROR_TOKEN_RESOLUTION

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

# region GIT_MIRROR_EXECUTION
## @purpose Execute git push --mirror to target org and verify HEAD consistency
echo "[IMP:9][context-promote] Promoting platform to context org: ${CONTEXT}"

TARGET_URL="https://github.com/${CONTEXT}/ai-platform.git"
echo "[IMP:8][context-promote] Target: github.com/${CONTEXT}/ai-platform.git"

# ──────────────────────────────────────────────────────────────────
# [IMP:9][MIRROR][PUSH] Push all refs to target via git push --mirror
# --mirror pushes all branches and tags, creating a complete replica
# ──────────────────────────────────────────────────────────────────
if git push --mirror "${TARGET_URL}" 2>&1; then
    echo "[IMP:9][context-promote] Push to ${CONTEXT}/ai-platform successful"

    # ──────────────────────────────────────────────────────────────
    # [IMP:8][MIRROR][VERIFY] Post-push HEAD verification
    # Compare remote HEAD (ls-remote) with local HEAD (rev-parse)
    # ──────────────────────────────────────────────────────────────
    MIRROR_HEAD=$(git ls-remote "${TARGET_URL}" HEAD | cut -f1)
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
else
    echo "[IMP:10][context-promote] FAILED: push to ${CONTEXT}/ai-platform failed" >&2
    exit 1
fi
# endregion GIT_MIRROR_EXECUTION
