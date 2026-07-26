#!/usr/bin/env python3
# GREP_SUMMARY: patch-basic-auth-provider NotImplementedError monkey-patch routes.py #55985
# STRUCTURE: ▶ read routes.py → ◇ find start_login() block → ⊕ replace with redirect → ⎋ write routes.py
# region MODULE_CONTRACT
## @purpose  Monkey-patch upstream Hermes Agent #55985: BasicAuthProvider.start_login()
##           raises NotImplementedError, causing GET /auth/login?provider=basic → 500.
## @scope    Applied at L1 Dockerfile build time; modifies /opt/hermes/hermes_cli/dashboard_auth/routes.py
## @invariants
##   - Idempotent: fails if target pattern not found (upstream may have changed)
##   - Single replacement: only first occurrence of the pattern is changed
##   - Exits 0 on success (patch applied or already applied), non-zero on failure
## @rationale Upstream PR #60978 not yet released in v2026.7.7.2.
##            For password-only providers (supports_password=True, no OAuth),
##            the auth_login endpoint must redirect to /login (which shows
##            the password form) instead of calling start_login().
## @changes 2026-07-24 — Created (BugFix: Hermes Dashboard login broken)
##           2026-07-24 — Updated: redirect to /login instead of ls=None
# endregion MODULE_CONTRACT

import sys

ROUTES_PATH = "/opt/hermes/hermes_cli/dashboard_auth/routes.py"

OLD_BLOCK = """    try:
        ls = p.start_login(redirect_uri=_redirect_uri(request))
    except ProviderError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider,
            reason="provider_unreachable",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Provider unreachable: {e}",
        )

    audit_log(
        AuditEvent.LOGIN_START,
        provider=provider,
        ip=_client_ip(request),
    )

    resp = RedirectResponse(url=ls.redirect_url, status_code=302)"""

NEW_BLOCK = """    try:
        ls = p.start_login(redirect_uri=_redirect_uri(request))
    except NotImplementedError:
        # Password-only providers (BasicAuthProvider) don't support OAuth
        # redirect flow. Redirect to /login which renders a password form.
        audit_log(
            AuditEvent.LOGIN_START,
            provider=provider,
            ip=_client_ip(request),
        )
        login_url = "/login"
        if next:
            from urllib.parse import quote
            login_url = f"/login?next={quote(next, safe='')}"
        return RedirectResponse(url=login_url, status_code=302)
    except ProviderError as e:
        audit_log(
            AuditEvent.LOGIN_FAILURE,
            provider=provider,
            reason="provider_unreachable",
            ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=503,
            detail=f"Provider unreachable: {e}",
        )

    audit_log(
        AuditEvent.LOGIN_START,
        provider=provider,
        ip=_client_ip(request),
    )

    resp = RedirectResponse(url=ls.redirect_url, status_code=302)"""


def apply_patch() -> bool:
    """Apply the monkey-patch. Returns True if patch applied, False if already patched."""
    with open(ROUTES_PATH) as f:
        content = f.read()

    if NEW_BLOCK in content:
        print("[PATCH:#55985] Patch already applied — idempotent skip", file=sys.stderr)
        return False

    if OLD_BLOCK not in content:
        print(
            "[PATCH:#55985] ERROR: Target pattern not found in routes.py — "
            "upstream may have changed. Patch NOT applied.",
            file=sys.stderr,
        )
        sys.exit(1)

    content = content.replace(OLD_BLOCK, NEW_BLOCK, 1)
    with open(ROUTES_PATH, "w") as f:
        f.write(content)

    print("[PATCH:#55985] BasicAuthProvider NotImplementedError fix v2 applied to routes.py", file=sys.stderr)
    return True


if __name__ == "__main__":
    apply_patch()
