#!/usr/bin/env python3
"""
# GREP_SUMMARY: domain_verifier, node-yaml, expose-domains, curl-verify, status-page, health-check
# STRUCTURE: ▶ ┌node_name,platform_root┐ → ○ resolve_node_yaml (3-path) → ○ get_expose_domains ┌projects[expose=true]┐
#            → ○ verify_domain foreach: ┌curl --max-time┐ → ◇ HTTP 200? → ⊕ pass|warn|connection_error
#            → ○ verify_status_page (Basic Auth) → ∑ results → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Post-deploy HTTP(S) domain verification — resolve node.yaml via 3-path search, parse
##            expose:true domains, curl each domain for HTTP 200, check status-page /health endpoint
## @scope    Business logic extracted from verify-domains.sh via Strangler-Fig (Wave 5a).
##           Called from verify-domains.sh shell facade or directly from Python tests.
## @invariants
##   - resolve_node_yaml: searches 3 paths (platform-local → org repos → VPS fallback)
##   - get_expose_domains: only extracts projects with `expose: true` (strict boolean)
##   - verify_domain: uses subprocess.run(["curl", ...]) — preserves same TLS stack as shell
##   - verify_status_page: Basic Auth via curl -u, URL = platform.{domain}/health
##   - Exit code 0: ALL domains + status-page respond HTTP 200
##   - Exit code 1: at least one domain unreachable, non-200, or status-page fails
##   - HTTPS is always used (https://${domain})
## @rationale Strangler-Fig migration per DevPlan 036A D1. Full Python port eliminates 2 inline
##            python3 -c blocks and enables unit-testing of all domain verification logic.
##            curl kept as subprocess (not requests) for TLS stack parity (DevPlan 036A D3).
## @changes  2026-07-26 | Wave 5a — Created via Strangler-Fig from verify-domains.sh (281→59 LOC)
# endregion MODULE_CONTRACT
"""

import argparse
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core.internal.shared.node_yaml import ConfigNotFoundError, ConfigParseError, NodeYaml

logger = logging.getLogger(__name__)

# ── Module-level constants ────────────────────────────────────────────────
CURL_TIMEOUT_DEFAULT = 10
STATUS_PAGE_TIMEOUT = 30
_LOG_FORMAT = "[IMP:%(imp)d][domain-verifier][%(funcName)s] %(message)s"


# region DATACLASSES


@dataclass
class VerifyResult:
    """Result of a single domain or status-page verification.

    ## @purpose  Structured result for machine parsing and summary aggregation
    ## @io ⇥ domain, status, http_code, error → ⎋ structured result object
    """

    domain: str
    """The domain or endpoint that was verified."""
    status: str
    """One of: 'pass' (HTTP 200), 'warn' (HTTP non-200), 'connection_error' (curl failed), 'skip' (no credentials)."""
    http_code: int | None = None
    """HTTP response code, or None on connection error / skip."""
    error: str | None = None
    """Error message on failure, None on success."""


# endregion DATACLASSES


# region LOGGING


class ImpLogFilter(logging.Filter):
    """Custom logging filter that injects IMP level from message prefix.

    ## @purpose  Enable `[IMP:N]` extraction for LDD telemetry compatibility.
    ## @io ⇥ record → ⎋ record augmented with imp attribute
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "[IMP:" in msg:
            try:
                record.imp = int(msg.split("[IMP:")[1].split("]")[0])
            except (IndexError, ValueError):
                record.imp = 0
        else:
            record.imp = 0
        return True


def _setup_logging() -> None:
    """Configure logging with IMP-compatible format.

    ## @purpose  Match log format expected by LDD telemetry and shell log_imp compatibility
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    handler.addFilter(ImpLogFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


# endregion LOGGING


# region FUNC_RESOLVE_NODE_YAML


def resolve_node_yaml(node_name: str, platform_root: Path) -> Path:
    """Resolve node.yaml via 3-path search.

    ## @purpose  Locate the node.yaml file for a given node by searching through
    ##            three ordered paths: platform-local → org repos → VPS fallback.
    ## @param node_name     Node name (directory name in node-configs/)
    ## @param platform_root Base path for platform-local lookup
    ## @returns Absolute Path to existing node.yaml
    ## @raises FileNotFoundError If none of the 3 paths contain the file
    ## @complexity O(1) — at most 3 path probes + optional glob

    ## @invariants
    ##   1. Path 1: {platform_root}/node-configs/{node_name}/node.yaml
    ##   2. Path 2: $HOME/projects/*/node-configs/{node_name}/node.yaml (glob)
    ##   3. Path 3: /opt/node-configs/{node_name}/node.yaml
    ##   4. First match wins — no merging
    ##   5. Raises FileNotFoundError with all 3 searched paths on no match
    """
    # Path 1: platform-local
    candidate = platform_root / "node-configs" / node_name / "node.yaml"
    logger.info("[IMP:8][resolve_node_yaml][path1] Checking: %s", candidate)
    if candidate.is_file():
        logger.info("[IMP:9][resolve_node_yaml][found] node.yaml found (path 1): %s", candidate)
        return candidate

    # Path 2: org repos (projects dir — glob match)
    home = Path.home()
    projects_dir = home / "projects"
    logger.info("[IMP:8][resolve_node_yaml][path2] Globbing: %s/*/node-configs/", projects_dir)
    if projects_dir.is_dir():
        for org_dir in projects_dir.iterdir():
            if org_dir.is_dir():
                candidate = org_dir / "node-configs" / node_name / "node.yaml"
                if candidate.is_file():
                    logger.info("[IMP:9][resolve_node_yaml][found] node.yaml found (path 2): %s", candidate)
                    return candidate

    # Path 3: VPS fallback
    candidate = Path("/opt/node-configs") / node_name / "node.yaml"
    logger.info("[IMP:8][resolve_node_yaml][path3] Checking: %s", candidate)
    if candidate.is_file():
        logger.info("[IMP:9][resolve_node_yaml][found] node.yaml found (path 3): %s", candidate)
        return candidate

    # None found — report all searched paths
    searched = [
        f"1. {platform_root}/node-configs/{node_name}/node.yaml",
        f"2. {home}/projects/*/node-configs/{node_name}/node.yaml",
        f"3. /opt/node-configs/{node_name}/node.yaml",
    ]
    msg = f"node.yaml not found for node={node_name}. Searched: {', '.join(searched)}"
    logger.error("[IMP:10][resolve_node_yaml][error] %s", msg)
    raise FileNotFoundError(msg)


# endregion FUNC_RESOLVE_NODE_YAML


# region FUNC_GET_EXPOSE_DOMAINS


def get_expose_domains(yaml_path: Path) -> list[str]:
    """Parse node.yaml and extract domains from projects with expose:true.

    ## @purpose  Read YAML file, filter projects where expose==True (strict boolean),
    ##            collect their domain values.
    ## @param yaml_path Path to node.yaml file
    ## @returns List of domain strings (may be empty)
    ## @complexity O(n) where n = number of projects in YAML
    ## @invariants
    ##   - Only boolean `True` matches — string "true" does NOT match
    ##   - Projects without a `domain` key are silently skipped
    ##   - Returns empty list if YAML is empty or has no projects key
    ##   - YAML parse errors propagate as exceptions
    """
    logger.info("[IMP:8][get_expose_domains][parse] Reading: %s", yaml_path)
    node = NodeYaml(yaml_path)
    projects = node.get_projects()
    domains: list[str] = []
    for p in projects:
        if p.get("expose", False) is True:
            domain = p.get("domain")
            if domain:
                domains.append(domain)

    if domains:
        logger.info("[IMP:9][get_expose_domains][result] Found %d expose:true domain(s): %s", len(domains), domains)
    else:
        logger.info("[IMP:9][get_expose_domains][result] No expose:true domains found — empty result")
    return domains


# endregion FUNC_GET_EXPOSE_DOMAINS


# region FUNC_VERIFY_DOMAIN


def verify_domain(domain: str, timeout: int = CURL_TIMEOUT_DEFAULT) -> VerifyResult:
    """Verify a single domain via curl HTTP HEAD request.

    ## @purpose  Run curl with --max-time to check if domain returns HTTP 200.
    ##            Connection failures (curl exit != 0) and non-200 codes are
    ##            distinguished in the returned VerifyResult.
    ## @param domain   Domain to check (https:// prefix is added internally)
    ## @param timeout  Curl --max-time in seconds (default 10)
    ## @returns VerifyResult with status: 'pass' (200), 'warn' (non-200), 'connection_error' (fail)
    ## @complexity O(1) — single subprocess call
    ## @invariants
    ##   - Curl is always used (not requests) — preserves TLS stack parity with shell
    ##   - Flags: -sS -o /dev/null -w '%{http_code}' --max-time {t}
    ##   - HTTPS only: https://{domain}
    ## @rationale D3: curl subprocess keeps same TLS stack (OpenSSL) as original verify-domains.sh
    """
    url = f"https://{domain}"
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        str(timeout),
        url,
    ]
    logger.info("[IMP:7][verify_domain][curl] Checking %s (timeout=%ds)", url, timeout)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][verify_domain][timeout] Connection timed out for %s", domain)
        return VerifyResult(
            domain=domain,
            status="connection_error",
            http_code=None,
            error=f"Connection timed out (>{timeout}s)",
        )

    if result.returncode != 0:
        logger.info("[IMP:9][verify_domain][fail] Connection failed for %s: curl exit %d", domain, result.returncode)
        return VerifyResult(
            domain=domain,
            status="connection_error",
            http_code=None,
            error=f"Connection failed (curl exit {result.returncode})",
        )

    try:
        http_code = int(result.stdout.strip())
    except ValueError:
        logger.error("[IMP:10][verify_domain][parse] Failed to parse HTTP code from: %s", result.stdout)
        return VerifyResult(
            domain=domain,
            status="connection_error",
            http_code=None,
            error=f"Failed to parse HTTP code: {result.stdout.strip()}",
        )

    if http_code == 200:
        logger.info("[IMP:9][verify_domain][pass] %s — HTTP 200 ✓", domain)
        return VerifyResult(domain=domain, status="pass", http_code=http_code)
    logger.info("[IMP:9][verify_domain][warn] %s — HTTP %d ⚠️ (expected 200)", domain, http_code)
    return VerifyResult(domain=domain, status="warn", http_code=http_code)


# endregion FUNC_VERIFY_DOMAIN


# region FUNC_VERIFY_STATUS_PAGE


def verify_status_page(
    platform_domain: str,
    email: str,
    password: str,
) -> VerifyResult | None:
    """Check status-page /health endpoint with Basic Auth.

    ## @purpose  Verify the status-page service is operational by curling
    ##            platform.{domain}/health with Basic Auth credentials.
    ##            Returns None if either credential is missing (skip).
    ## @param platform_domain Platform apex domain (e.g. tronyx.ru)
    ## @param email           Basic Auth username (PLATFORM_MASTER_EMAIL)
    ## @param password        Basic Auth password (PLATFORM_MASTER_PASSWORD)
    ## @returns VerifyResult with status 'pass'/'connection_error'/'warn', or None if skipped
    ## @complexity O(1) — single subprocess call
    ## @invariants
    ##   - URL is platform.{domain}/health (NOT apex domain)
    ##   - Requires both email and password to be non-empty
    ##   - Returns None (skip) when credentials are missing — not an error

    ## ⚠️ TRAP[BUG] · 2026-07-24 · P2 · status-page URL mismatch
    ## · Symptom: curl https://tronyx.ru/health → nginx overlay proxied to tronyx-site project → 500
    ## · Root: status-page lives on platform.tronyx.ru (platform-vhost.conf), not apex domain
    ## · Fix: use platform.{PLATFORM_DOMAIN}/health instead of {PLATFORM_DOMAIN}/health
    ## · @see DevPlan 051 P2
    ## (перенесён из verify-domains.sh L194-198)
    """
    if not email or not password:
        logger.info("[IMP:9][verify_status_page][skip] Missing credentials — skipping status-page health check")
        return None

    status_page_url = f"https://platform.{platform_domain}/health"
    cmd = [
        "curl",
        "-sS",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "--max-time",
        str(STATUS_PAGE_TIMEOUT),
        "-u",
        f"{email}:{password}",
        status_page_url,
    ]
    logger.info("[IMP:7][verify_status_page][curl] Checking %s", status_page_url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=STATUS_PAGE_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        logger.error("[IMP:10][verify_status_page][timeout] Status-page timed out for %s", platform_domain)
        return VerifyResult(
            domain=status_page_url,
            status="connection_error",
            http_code=None,
            error=f"Status-page timed out (>{STATUS_PAGE_TIMEOUT}s)",
        )

    if result.returncode != 0:
        logger.error("[IMP:9][verify_status_page][fail] Status-page connection failed: curl exit %d", result.returncode)
        return VerifyResult(
            domain=status_page_url,
            status="connection_error",
            http_code=None,
            error=f"Connection failed (curl exit {result.returncode})",
        )

    try:
        http_code = int(result.stdout.strip())
    except ValueError:
        logger.error("[IMP:10][verify_status_page][parse] Failed to parse HTTP code: %s", result.stdout)
        return VerifyResult(
            domain=status_page_url,
            status="connection_error",
            http_code=None,
            error=f"Failed to parse HTTP code: {result.stdout.strip()}",
        )

    if http_code == 200:
        logger.info("[IMP:9][verify_status_page][pass] Status-page /health — HTTP 200 PASS ✓")
        return VerifyResult(domain=status_page_url, status="pass", http_code=http_code)
    logger.error("[IMP:9][verify_status_page][fail] Status-page /health — HTTP %d FAIL ⚠️", http_code)
    return VerifyResult(domain=status_page_url, status="warn", http_code=http_code)


# endregion FUNC_VERIFY_STATUS_PAGE


# region FUNC_MAIN


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for domain verification.

    ## @purpose  Parse command-line arguments, orchestrate full verification pipeline:
    ##            resolve node.yaml → parse expose:true domains → curl each → status-page check.
    ## @param argv  Argument list (defaults to sys.argv[1:])
    ## @returns Exit code 0 (all pass) or 1 (any fail)
    ## @complexity O(n) where n = number of domains + 1 status-page check
    ## @invariants
    ##   - Requires --node and --platform-root arguments
    ##   - Reads PLATFORM_DOMAIN, PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD from env for status-page
    ##   - Prints per-domain status lines to stdout (same format as shell version)
    ##   - Exit 0: ALL domains + status-page pass
    ##   - Exit 1: any domain or status-page fails
    """
    _setup_logging()

    parser = argparse.ArgumentParser(description="Domain verification tool (Wave 5a)")
    parser.add_argument("command", choices=["verify"], help="Subcommand (only 'verify' supported)")
    parser.add_argument("--node", required=True, help="Node name")
    parser.add_argument("--platform-root", default="/opt/platform", help="Platform root path")
    parser.add_argument("--curl-timeout", type=int, default=CURL_TIMEOUT_DEFAULT, help="Curl timeout in seconds")

    args = parser.parse_args(argv)

    node_name = args.node
    platform_root = Path(args.platform_root)
    curl_timeout = args.curl_timeout

    logger.info("[IMP:7][main][start] Starting post-deploy verification for node=%s", node_name)

    # Step 1: Resolve node.yaml
    try:
        yaml_path = resolve_node_yaml(node_name, platform_root)
    except FileNotFoundError as e:
        logger.error("[IMP:10][main][error] %s", str(e))
        return 1
    logger.info("[IMP:7][main][resolve] Resolved node.yaml: %s", yaml_path)

    # Step 2: Parse expose:true domains
    logger.info("[IMP:7][main][parse] Parsing projects with expose:true from %s", yaml_path)
    try:
        domains = get_expose_domains(yaml_path)
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.error("[IMP:10][main][parse] Failed to parse YAML: %s — %s", yaml_path, e)
        return 1

    # Step 3: Verify each domain
    all_ok = True

    if not domains:
        logger.info("[IMP:9][main][ok] No expose:true domains found — nothing to verify")
        print("")
        logger.info("[IMP:9][main][ok] ALL DOMAINS PASS — 0 domain(s) to check")
    else:
        logger.info("[IMP:7][main][domains] Found %d expose:true domain(s) to check", len(domains))
        for d in domains:
            logger.info("[IMP:8][main][domain]   - %s", d)

        for domain in domains:
            result = verify_domain(domain, timeout=curl_timeout)
            if result.status == "pass":
                print(f"  {domain} -> HTTP {result.http_code} ✓")
            elif result.status == "connection_error":
                print(f"  {domain} -> {result.error}")
                all_ok = False
            else:
                print(f"  {domain} -> HTTP {result.http_code} ⚠️  WARN: expected 200")
                all_ok = False

        print("")
        if all_ok:
            logger.info("[IMP:9][main][ok] ALL DOMAINS PASS — HTTP 200 for all %d domain(s)", len(domains))
        else:
            logger.info("[IMP:9][main][fail] SOME DOMAINS FAILED — review output above")

    # Step 4: Status-page health check
    platform_domain = os.environ.get("PLATFORM_DOMAIN", "")
    master_email = os.environ.get("PLATFORM_MASTER_EMAIL", "")
    master_password = os.environ.get("PLATFORM_MASTER_PASSWORD", "")

    sp_result = verify_status_page(platform_domain, master_email, master_password)
    if sp_result is not None:
        if sp_result.status == "pass":
            print("  status-page /health -> HTTP 200 PASS ✓")
            logger.info("[IMP:7][main][status-page] Status-page health check PASSED")
        elif sp_result.status == "connection_error":
            print(f"  status-page /health -> {sp_result.error}")
            logger.error("[IMP:9][main][status-page] Status-page health check FAILED — connection error")
            all_ok = False
        else:
            print(f"  status-page /health -> HTTP {sp_result.http_code} FAIL ⚠️")
            logger.error("[IMP:9][main][status-page] Status-page health check FAILED (HTTP %s)", sp_result.http_code)
            all_ok = False
    else:
        logger.info(
            "[IMP:8][main][status-page] Skipping status-page health check — missing PLATFORM_DOMAIN or credentials"
        )

    if sp_result is not None:
        print("")

    # Step 5: Final verdict
    if all_ok:
        logger.info("[IMP:9][main][verdict] ALL CHECKS PASS — domains + status-page health")
        return 0
    logger.info("[IMP:9][main][verdict] SOME CHECKS FAILED — review output above")
    return 1


# endregion FUNC_MAIN


if __name__ == "__main__":
    sys.exit(main())
