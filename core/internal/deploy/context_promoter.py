#!/usr/bin/env python3
# GREP_SUMMARY: context-promote git-mirror ssh https askpass audit context_promoter deploy push ls-remote mirror-verify
# STRUCTURE: ▶ check_ssh_available ◇ → ├─ SSH: git push --mirror git@github.com:<ctx>/ai-platform.git ┤ └─ HTTPS: GIT_ASKPASS tempfile → push --mirror https://github.com/<ctx>/ai-platform.git ┤ → ls-remote HEAD → ◇ verify mirror==source → audit DONE/FAIL → ⎋ exit 0/1
# region MODULE_CONTRACT
## @purpose  Promote the ai-platform to a context GitHub org via `git push --mirror`.
##           Business logic extracted from core/entrypoints/context-promote.sh (161 LOC,
##           DevPlan 103 Strangler-Fig): SSH primary channel, HTTPS+GIT_ASKPASS fallback,
##           post-push HEAD mirror verification, JSON-lines audit trail.
## @scope    Single consumer: core/entrypoints/context-promote.sh (thin facade).
##           Invoked as `python3 -m core.internal.deploy.context_promoter <CONTEXT>`.
##           CONTEXT from argv[1], GIT_MIRROR_TOKEN from environment (never argv).
## @invariants
##   1. SSH primary: `ssh -T -o ConnectTimeout=<SSH_CONNECT_TIMEOUT> -o BatchMode=yes git@github.com` —
##      availability determined by AUTH MESSAGE in output, NOT exit code
##      (`ssh -T git@github.com` exits 1 even on success — GitHub provides no shell).
##   2. HTTPS fallback ONLY when SSH unavailable AND GIT_MIRROR_TOKEN set; else FATAL exit 1.
##   3. GIT_ASKPASS temp script contains the LITERAL `${GIT_MIRROR_TOKEN}` (variable name),
##      never the token value — the token stays in process env, never on disk/argv/URL (AC2/AC7).
##   4. Mirror verification: `git ls-remote <target> HEAD` == `git rev-parse HEAD` (AC6).
##   5. Audit entries (START/DONE/FAIL) via shared/audit_logger.write_audit_entry()
##      with tag `context-promote:<ctx>`; log file overridable via AUDIT_LOG_FILE env.
##   6. Token never appears in process list, shell history, or git URL.
## @rationale DevPlan 103: last entrypoint >100 LOC without a Python module. The GIT_ASKPASS
##            heredoc generation was a Tier-1 Strangler trigger (heredoc with business logic).
##            Python tempfile + subprocess env removes the heredoc/trap pattern;
##            write_audit_entry() replaces the shell audit_step wrapper (D3).
##            SSH primary because context-promote runs locally at the operator machine —
##            ssh-agent has the operator key; node secrets (GIT_MIRROR_TOKEN) are
##            unavailable locally (B4 root cause). HTTPS fallback keeps PAT-based use possible.
## @changes  2026-07-31 | DevPlan 103 — extracted from core/entrypoints/context-promote.sh (161 LOC)
##           2026-08-01 | DevPlan 116 B5 T2 — ConnectTimeout outlier (10) → SSH_CONNECT_TIMEOUT=30 (U-15)
## 🧐 TRAP[DECISION] · 2026-07-18 · — · SSH primary, HTTPS fallback
## · Rejected: HTTPS-only (required token, unavailable locally — B4)
## · Reason: context-promote runs locally at operator — ssh-agent has operator key,
##   node secrets (GIT_MIRROR_TOKEN) unavailable. SSH is zero-config for operator.
## · Rev: if CI-driven context-promote is introduced (runner without SSH key) → promote
##   HTTPS+token to primary, SSH to optional.
# endregion MODULE_CONTRACT

import logging
import os
import subprocess
import sys
import tempfile

from core.internal.shared import audit_logger
from core.internal.shared.exceptions import ConfigValidationError

# DevPlan 116 B5 T1/T2: единый ConnectTimeout (U-15) — литерал 10 outlier заменён
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

# GitHub SSH auth greeting markers — identical to the legacy shell grep
# `grep -q "successfully authenticated\|Hi.*"` over the merged 2>&1 output.
_SSH_AUTH_MARKERS = ("successfully authenticated", "Hi ")


# region FUNC_check_ssh_available
def check_ssh_available() -> bool:
    """Determine whether the operator's SSH key authenticates to github.com.

    ▶ ┌ssh -T git@github.com (ConnectTimeout=<SSH_CONNECT_TIMEOUT>, BatchMode=yes)┐ → ○ merge stdout+stderr → ◇ auth marker? → ⎋ bool

    ## @purpose — SSH primary-channel availability probe. Mirrors the legacy shell check
    ##            (context-promote.sh:50-60) which greps the merged `2>&1` output for the
    ##            GitHub auth greeting.
    ## @io — ⇥ None → ⎋ bool — True when output contains an auth marker
    ## @complexity — O(1) subprocess + O(len(output))
    ## @invariants
    ##   - Availability is CONTENT-based, not exit-code-based: `ssh -T git@github.com`
    ##     ALWAYS exits 1 on success (GitHub provides no shell) — exit code is not a signal.
    ##   - Both stdout and stderr are inspected (SSH writes the auth greeting to stderr;
    ##     the legacy shell merged streams with 2>&1).
    ##   - Missing ssh binary (FileNotFoundError) → False, never raises.
    """
    try:
        result = subprocess.run(
            [
                "ssh",
                "-T",
                "-o",
                f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
                "-o",
                "BatchMode=yes",
                "git@github.com",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("[IMP:7][check_ssh_available] ssh binary not found — SSH unavailable")
        return False

    combined = f"{result.stdout}\n{result.stderr}"
    # ⚠️ Exit code deliberately IGNORED: `ssh -T git@github.com` exits 1 even when
    # authenticated ("no shell access"). The auth-message marker is the only reliable
    # signal — identical to the legacy content-based grep over 2>&1.
    if any(marker in combined for marker in _SSH_AUTH_MARKERS):
        logger.info("[IMP:8][check_ssh_available] SSH key for github.com available — will use SSH primary channel")
        return True

    logger.info("[IMP:8][check_ssh_available] SSH key not available or timeout — will attempt fallback")
    return False


# endregion FUNC_check_ssh_available


# region FUNC_promote_via_ssh
def promote_via_ssh(context: str) -> str:
    """Mirror all refs to <context>/ai-platform via SSH; return remote HEAD sha.

    ▶ ┌git push --mirror git@github.com:<ctx>/ai-platform.git┐ → ○ git ls-remote <target> HEAD → ⊕ split()[0] → ⎋ MIRROR_HEAD

    ## @purpose — Complete ref synchronization (all branches + tags) to the context
    ##            GitHub org over SSH using the operator's ssh-agent key.
    ## @io — ⇥ context: str — GitHub org name → ⎋ str — remote HEAD sha (MIRROR_HEAD)
    ## @complexity — O(refs) push + O(1) ls-remote
    ## @invariants
    ##   - `git push --mirror` raises CalledProcessError on non-zero exit (check=True)
    ##   - Empty ls-remote HEAD (empty target repo) → returns "" (surfaces as mirror mismatch later)
    ##   - Target repo must already exist (created by `make new-context`)
    """
    target = f"git@github.com:{context}/ai-platform.git"
    logger.info("[IMP:9][promote_via_ssh] Promoting platform to context org: %s", context)
    logger.info("[IMP:8][promote_via_ssh] SSH target: %s", target)

    subprocess.run(
        ["git", "push", "--mirror", target],
        check=True,
        capture_output=True,
        text=True,
    )
    logger.info("[IMP:9][promote_via_ssh] SSH push to %s/ai-platform successful", context)

    ls = subprocess.run(
        ["git", "ls-remote", target, "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    parts = ls.stdout.split()
    mirror_head = parts[0] if parts else ""
    logger.info("[IMP:8][promote_via_ssh] Mirror HEAD (ls-remote): %s", mirror_head[:7])
    return mirror_head


# endregion FUNC_promote_via_ssh


# region FUNC_promote_via_https
def promote_via_https(context: str, token: str) -> str:
    """Mirror all refs via HTTPS with GIT_ASKPASS credential delivery; return remote HEAD sha.

    ▶ ┌NamedTemporaryFile(delete=False) → literal '#!/bin/sh echo ${GIT_MIRROR_TOKEN}'┐ → ○ chmod 700 → ⊕ env={os.environ, GIT_ASKPASS} → ⚡ git push --mirror https://github.com/<ctx>/ai-platform.git → ○ ls-remote → ⎷ finally: os.unlink → ⎋ MIRROR_HEAD

    ## @purpose — HTTPS fallback channel: git prompts for credentials and the GIT_ASKPASS
    ##            script answers from the GIT_MIRROR_TOKEN env var. The token value NEVER
    ##            touches disk, argv, or the git URL (AC2/AC7).
    ## @io — ⇥ context: str — GitHub org name; token: str — GIT_MIRROR_TOKEN (non-empty)
    ##       → ⎋ str — remote HEAD sha (MIRROR_HEAD)
    ## @complexity — O(refs) push + O(1) ls-remote
    ## @invariants
    ##   - Temp script content is the LITERAL `${GIT_MIRROR_TOKEN}` — NOT an f-string,
    ##     NOT the token value. Git runs the script via /bin/sh, which expands the variable
    ##     from its own environment (inherited via os.environ).
    ##   - Temp file created 0600 (NamedTemporaryFile) then chmod 0700 — never world-readable.
    ##   - os.unlink(temp_path) in finally — cleanup on success AND failure (no trap EXIT).
    ##   - Raises ValueError on empty token (fail-fast); CalledProcessError on push failure.
    """
    if not token:
        raise ConfigValidationError("GIT_MIRROR_TOKEN is empty — HTTPS fallback impossible")

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as askpass:
            # ⚠️ LITERAL string, NOT an f-string: writes the variable NAME `${GIT_MIRROR_TOKEN}`.
            # The token VALUE never leaves os.environ — QA Review DevPlan 103 D4 / AC7.
            askpass.write('#!/bin/sh\necho "${GIT_MIRROR_TOKEN}"\n')
            temp_path = askpass.name
        os.chmod(temp_path, 0o700)

        env = {**os.environ, "GIT_ASKPASS": temp_path}
        target = f"https://github.com/{context}/ai-platform.git"
        logger.info("[IMP:8][promote_via_https] GIT_ASKPASS set up at %s", temp_path)
        logger.info("[IMP:8][promote_via_https] HTTPS target: %s", target)

        subprocess.run(
            ["git", "push", "--mirror", target],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("[IMP:9][promote_via_https] HTTPS push to %s/ai-platform successful", context)

        ls = subprocess.run(
            ["git", "ls-remote", target, "HEAD"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        parts = ls.stdout.split()
        mirror_head = parts[0] if parts else ""
        logger.info("[IMP:8][promote_via_https] Mirror HEAD (ls-remote): %s", mirror_head[:7])
        return mirror_head
    finally:
        if temp_path is not None:
            os.unlink(temp_path)
            logger.info("[IMP:8][promote_via_https] Removed GIT_ASKPASS tempfile %s", temp_path)


# endregion FUNC_promote_via_https


# region FUNC_verify_mirror
def verify_mirror(context: str, mirror_head: str, source_head: str) -> bool:
    """Compare remote mirror HEAD against local HEAD — post-push verification (AC6).

    ▶ ┌mirror_head, source_head┐ → ◇ equal? → ├─yes: [IMP:9] Mirror sync verified ┤ └─no: [IMP:10] FAIL → ⎋ bool

    ## @purpose — Ensure the mirror is consistent before declaring success: the promoted
    ##            org's HEAD must equal the local repository HEAD.
    ## @io — ⇥ context: str — GitHub org (diagnostics only); mirror_head: str — ls-remote HEAD;
    ##       source_head: str — git rev-parse HEAD → ⎋ bool — True when heads match
    ## @complexity — O(1)
    ## @invariants
    ##   - Match → IMP:9 log, True; mismatch → IMP:10 log, False (never raises)
    ##   - Empty mirror_head (empty target repo) is a mismatch → False
    """
    logger.info("[IMP:8][verify_mirror] Source HEAD: %s", source_head)
    logger.info("[IMP:8][verify_mirror] Mirror HEAD: %s", mirror_head)

    if mirror_head == source_head:
        logger.info("[IMP:9][verify_mirror] Mirror sync verified: %s", source_head[:7])
        return True

    logger.error(
        "[IMP:10][verify_mirror] FAIL: mirror HEAD (%s) != source HEAD (%s)",
        mirror_head[:7],
        source_head[:7],
    )
    return False


# endregion FUNC_verify_mirror


# region FUNC_promote_context
def promote_context(context: str, token: str | None) -> int:
    """Orchestrator: audit START → channel selection → push → verify → audit DONE/FAIL → exit code.

    ▶ write_audit_entry(START) → ◇ check_ssh_available → ├─SSH: promote_via_ssh ┤ └─HTTPS: promote_via_https (token required) → git rev-parse HEAD → ◇ verify_mirror → audit DONE/FAIL → ⎋ 0|1

    ## @purpose — Single entry point for `make context-promote`: full lifecycle with audit
    ##            trail and fail-fast diagnostics. Returns the process exit code (0/1).
    ## @io — ⇥ context: str — GitHub org name; token: str | None — GIT_MIRROR_TOKEN or None
    ##       → ⎋ int — 0 success, 1 failure
    ## @complexity — O(refs) push + O(1) verification
    ## @invariants
    ##   - Audit: START at entry; DONE (rc=0) or FAIL on every non-zero return path
    ##   - Fail-fast: SSH unavailable AND token missing → IMP:10 FATAL, exit 1 (no push attempted)
    ##   - CalledProcessError / OSError from git/ssh → IMP:10 FAILED + audit FAIL + return 1
    ##   - Audit log file: AUDIT_LOG_FILE env override, else audit_logger.DEFAULT_LOG_FILE
    ##   - Never raises on operational failures — always returns 0/1
    """
    if not context:
        logger.error(
            "[IMP:10][promote_context] ERROR: CONTEXT required — usage: make context-promote CONTEXT=<context>"
        )
        return 1

    log_file = os.environ.get("AUDIT_LOG_FILE", audit_logger.DEFAULT_LOG_FILE)
    tag = f"context-promote:{context}"
    audit_logger.write_audit_entry(
        tag, "START", f"starting context promote to {context}/ai-platform", log_file=log_file
    )

    if check_ssh_available():
        logger.info("[IMP:9][promote_context] Promoting platform to context org: %s", context)
        try:
            mirror_head = promote_via_ssh(context)
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.error("[IMP:10][promote_context] FAILED: SSH push to %s/ai-platform failed: %s", context, exc)
            logger.error(
                "[IMP:10][promote_context] Check that target org %s/ai-platform exists and operator has push access",
                context,
            )
            logger.error("[IMP:10][promote_context] FATAL: create %s/ai-platform first", context)
            audit_logger.write_audit_entry(tag, "FAIL", f"SSH push failed ({exc})", log_file=log_file)
            return 1
    else:
        logger.info("[IMP:8][promote_context] SSH unavailable — falling back to HTTPS+token")
        if not token:
            logger.error("[IMP:10][promote_context] FATAL: SSH unavailable AND GIT_MIRROR_TOKEN not set")
            logger.error(
                "[IMP:10][promote_context] Either ensure ssh-agent has a key for git@github.com, "
                "or set GIT_MIRROR_TOKEN PAT"
            )
            audit_logger.write_audit_entry(
                tag, "FAIL", "FATAL: SSH unavailable and GIT_MIRROR_TOKEN not set", log_file=log_file
            )
            return 1
        try:
            mirror_head = promote_via_https(context, token)
        except (subprocess.CalledProcessError, OSError) as exc:
            logger.error("[IMP:10][promote_context] FAILED: HTTPS push to %s/ai-platform failed: %s", context, exc)
            logger.error("[IMP:10][promote_context] FATAL: create %s/ai-platform first", context)
            audit_logger.write_audit_entry(tag, "FAIL", f"HTTPS push failed ({exc})", log_file=log_file)
            return 1

    try:
        source_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("[IMP:10][promote_context] FAILED: cannot resolve local HEAD via git rev-parse: %s", exc)
        audit_logger.write_audit_entry(tag, "FAIL", f"git rev-parse HEAD failed ({exc})", log_file=log_file)
        return 1

    if verify_mirror(context, mirror_head, source_head):
        logger.info("[IMP:9][promote_context] SUCCESS: platform promoted to %s/ai-platform", context)
        audit_logger.write_audit_entry(tag, "DONE", "completed successfully (rc=0)", log_file=log_file)
        return 0

    logger.error("[IMP:10][promote_context] FAIL: mirror HEAD != source HEAD — mirror verification failed")
    audit_logger.write_audit_entry(tag, "FAIL", "mirror verification failed: HEAD mismatch", log_file=log_file)
    return 1


# endregion FUNC_promote_context


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point: CONTEXT from argv[1], GIT_MIRROR_TOKEN from environment.

    ▶ ┌sys.argv[1:]┐ → ◇ empty? → [IMP:10] ERROR exit 1 → ○ promote_context(context, os.environ.get("GIT_MIRROR_TOKEN")) → ⎋ rc

    ## @purpose — Process the CLI invocation: parse context, read token from env, delegate
    ##            to promote_context(). Called via `python3 -m core.internal.deploy.context_promoter <CONTEXT>`.
    ## @io — ⇥ argv: list[str] | None — CLI args (default sys.argv[1:]) → ⎋ int — exit code
    ## @complexity — O(1) + promote_context
    ## @invariants
    ##   - Missing CONTEXT → IMP:10 ERROR + return 1 (never IndexError)
    ##   - Token read from os.environ only — never accepted via argv (AC2/AC7)
    ##   - __main__ block translates the return code via sys.exit() → SystemExit(rc)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        logger.error(
            "[IMP:10][main] ERROR: CONTEXT required — usage: python3 -m core.internal.deploy.context_promoter <CONTEXT>"
        )
        return 1
    context = argv[0]
    token = os.environ.get("GIT_MIRROR_TOKEN")
    return promote_context(context, token)


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
