#!/usr/bin/env python3
# GREP_SUMMARY: preflight, gate, ssh-probe, disk-space, s3-probe, ghcr-probe, dockerhub, dns-resolution, graceful-degradation, fatal-warn
# STRUCTURE: ▶ ┌node.yaml + context┐ → ○ run_all_checks(ssh|disk|s3|ghcr|dockerhub|dns) → ◇ classify(FATAL/WARN) → ⊕ JSON stdout → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Pre-flight checks executed BEFORE the bootstrap state machine starts.
##           Probes SSH connectivity, disk space, S3/ghcr.io/Docker Hub availability,
##           and DNS resolution. FATAL checks abort bootstrap; WARN checks degrade gracefully.
## @scope    Called from node-lifecycle.sh main() (init mode) before the first checkpoint_step.
##           NOT invoked during --dry-run or --resume. Exits 0 if all FATAL checks pass.
## @invariants
##   1. FATAL checks: ssh_connectivity, disk_space → exit 1 on failure
##   2. WARN checks: s3, ghcr, docker_hub, dns → add to warnings list, continue
##   3. Each probe has a 10s timeout to avoid hanging bootstrap
##   4. Output: JSON dict to stdout {check_name: {status, latency_ms, detail}}
##   5. status ∈ {"ok", "warn", "fatal"}
##   6. Idempotent: safe to run multiple times (no side effects beyond probes)
##   7. Does NOT mutate node state or filesystem
## @rationale StatusReport 045 revealed that bootstrap proceeds even when ghcr.io/S3 are
##           unreachable, causing late failures at deploy time. A pre-flight gate surfaces
##           these issues early with actionable diagnostics. Graceful degradation (WARN)
##           for non-essential services preserves bootstrap resilience.
## @changes  2026-07-22 | DevPlan 047 Phase 1 — Created pre-flight gate module
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from core.internal.config import platform_config

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────
PROBE_TIMEOUT = 10  # seconds per probe
DISK_MIN_GB = 10  # minimum free disk space in GB at /opt
DISK_MIN_BYTES = DISK_MIN_GB * 1024 * 1024 * 1024

# Checks that abort bootstrap on failure
FATAL_CHECKS = ("ssh_connectivity", "disk_space")
# Checks that log a warning but allow bootstrap to continue
WARN_CHECKS = ("s3_connectivity", "ghcr_auth", "docker_hub_probe", "dns_resolution")


# region DATACLASSES


@dataclass
class CheckResult:
    """Result of a single pre-flight check.

    ## @purpose — Capture the outcome, timing, and detail of one probe.
    ## @io — ⇥ constructor params → ⎋ serializable CheckResult
    ## @complexity — O(1)
    """

    status: str = "ok"  # ok | warn | fatal
    latency_ms: int = 0
    detail: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        if self.error is None:
            d.pop("error", None)
        return d


@dataclass
class PreflightResult:
    """Aggregated result of all pre-flight checks.

    ## @purpose — Collect all check results and expose fatal/warn classification.
    ## @io — ⇥ checks dict → ⎋ serializable result with summary
    ## @complexity — O(N) where N = number of checks
    """

    checks: dict[str, CheckResult] = field(default_factory=dict)
    fatals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, name: str, result: CheckResult) -> None:
        """Add a check result and classify it."""
        self.checks[name] = result
        if result.status == "fatal":
            self.fatals.append(name)
        elif result.status == "warn":
            self.warnings.append(name)

    def has_fatals(self) -> bool:
        """Return True if any FATAL check failed."""
        return len(self.fatals) > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {name: result.to_dict() for name, result in self.checks.items()}


# endregion DATACLASSES


# region PROBE_FUNCTIONS


# region FUNC_probe_ssh_connectivity
## @purpose — Probe SSH connectivity by checking the local SSH service and node availability.
##            On the bootstrap node itself, this verifies the sshd socket is listening on port 22.
## @io — ⇥ host: str (default 127.0.0.1), port: int (default 22) → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - FATAL: if sshd not listening → bootstrap cannot proceed
##   - Uses socket connection with PROBE_TIMEOUT
def probe_ssh_connectivity(host: str = "127.0.0.1", port: int = 22) -> CheckResult:
    """Probe SSH connectivity via TCP socket check. FATAL on failure."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            latency = int((time.monotonic() - start) * 1000)
            logger.info("[IMP:9][preflight][ssh] SSH probe OK (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail=f"SSH reachable at {host}:{port}")
    except (TimeoutError, ConnectionRefusedError, OSError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="fatal",
            latency_ms=latency,
            detail=f"SSH unreachable at {host}:{port}",
            error=str(e),
        )


# endregion FUNC_probe_ssh_connectivity


# region FUNC_probe_disk_space
## @purpose — Probe available disk space at a target path (default /opt).
##            FATAL if free space < 10 GB — bootstrap needs room for images, volumes, secrets.
## @io — ⇥ path: str (default /opt) → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - FATAL: if free_bytes < DISK_MIN_BYTES (10 GB)
##   - Uses shutil.disk_usage (no subprocess call)
def probe_disk_space(path: str = "/opt") -> CheckResult:
    """Probe free disk space. FATAL if below threshold."""
    import shutil

    start = time.monotonic()
    try:
        # Use parent if path doesn't exist (common during pre-bootstrap)
        probe_path = path
        while not os.path.exists(probe_path) and probe_path != "/":
            probe_path = os.path.dirname(probe_path)

        usage = shutil.disk_usage(probe_path)
        latency = int((time.monotonic() - start) * 1000)
        free_gb = round(usage.free / (1024 * 1024 * 1024), 1)
        if usage.free < DISK_MIN_BYTES:
            return CheckResult(
                status="fatal",
                latency_ms=latency,
                detail=f"Disk space at {probe_path}: {free_gb} GB free (need ≥{DISK_MIN_GB} GB)",
                error=f"insufficient_disk_space: {free_gb} GB < {DISK_MIN_GB} GB",
            )
        logger.info("[IMP:9][preflight][disk] Disk OK: %s has %s GB free", probe_path, free_gb)
        return CheckResult(status="ok", latency_ms=latency, detail=f"Disk space at {probe_path}: {free_gb} GB free")
    except OSError as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="fatal",
            latency_ms=latency,
            detail=f"Cannot probe disk at {path}",
            error=str(e),
        )


# endregion FUNC_probe_disk_space


# region FUNC_probe_s3_connectivity
## @purpose — Probe S3 connectivity by attempting a HEAD bucket request.
##            WARN if S3 unavailable — cert restore will fall back to acme.sh.
## @io — ⇥ endpoint: str, bucket: str, access_key: str, secret_key: str → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if S3 unreachable → graceful degradation (cert_orchestrator handles fallback)
##   - If credentials missing → WARN (not fatal — S3 is optional)
def probe_s3_connectivity(
    endpoint: str = "",
    bucket: str = "",
    access_key: str = "",
    secret_key: str = "",
) -> CheckResult:
    """Probe S3 connectivity via boto3 head_bucket. WARN on failure."""
    start = time.monotonic()
    if not bucket or not access_key or not secret_key:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 credentials not configured — cert restore will use acme.sh only",
        )
    try:
        # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · lazy-импорт s3_client (boto3) — RC-сессия 121
        # · Symptom: preflight PanicException pyo3_runtime на свежей ноде — module-level импорт
        #   s3_client тянул boto3→pyopenssl(debian)→cryptography(pip) несовместимость.
        # · Fix: импорт ВНУТРИ probe (префлайт — лёгкая диагностика, не должен тянуть boto3);
        #   ImportError → WARN (boto3 появится в φ1 python_deps — preflight идёт ДО него).
        from botocore.exceptions import ClientError  # type: ignore[import-untyped]

        from core.internal.shared.s3_client import get_s3_client as _shared_get_s3_client

        ep = endpoint or os.environ.get("S3_ENDPOINT_URL", "https://s3.timeweb.cloud")
        # DevPlan 117 D26: клиент создаётся через shared/s3_client.get_s3_client
        # (max_attempts=1 — быстрый probe; proxy-stripping не требуется для head_bucket)
        client = _shared_get_s3_client(endpoint=ep, access_key=access_key, secret_key=secret_key, max_attempts=1)
        client.head_bucket(Bucket=bucket)
        latency = int((time.monotonic() - start) * 1000)
        logger.info("[IMP:9][preflight][s3] S3 probe OK (%dms) — bucket %s reachable", latency, bucket)
        return CheckResult(status="ok", latency_ms=latency, detail=f"S3 bucket {bucket} reachable at {ep}")
    except ImportError:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="boto3 not installed — S3 cert restore unavailable",
        )
    except ClientError as e:
        latency = int((time.monotonic() - start) * 1000)
        code = e.response.get("Error", {}).get("Code", "Unknown")
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"S3 head_bucket failed (code={code}) — cert restore degraded",
            error=str(e)[:200],
        )
    except (OSError, ConnectionError, TimeoutError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 probe error — cert restore degraded",
            error=str(e)[:200],
        )
    except BaseException as e:
        latency = int((time.monotonic() - start) * 1000)
        logger.warning(
            "[IMP:8][preflight][s3] S3 probe crashed (%s: %s) — WARN, bootstrap continues",
            type(e).__name__,
            str(e)[:100],
        )
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="S3 probe crashed — cert restore degraded",
            error=f"{type(e).__name__}: {str(e)[:100]}",
        )


# endregion FUNC_probe_s3_connectivity


# region FUNC_probe_ghcr_auth
## @purpose — Probe ghcr.io authentication by attempting a docker manifest inspect.
##            WARN if ghcr.io unavailable — context_deployer will fall back to on-node build.
## @io — ⇥ token: str (GHCR pull token) → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if ghcr.io unreachable → context_deployer uses build fallback
##   - If token missing → WARN (not fatal — build fallback is valid)
def probe_ghcr_auth(token: str = "") -> CheckResult:
    """Probe ghcr.io auth via docker manifest inspect. WARN on failure."""
    start = time.monotonic()
    if not token:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="GHCR_PULL_TOKEN not set — context deploy will use build fallback",
        )
    try:
        # Use a lightweight image (hello-world) to test ghcr.io reachability
        result = subprocess.run(
            ["bash", "-c", f"echo '{token}' | docker login ghcr.io -u x-access-token --password-stdin 2>&1"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
        latency = int((time.monotonic() - start) * 1000)
        if result.returncode == 0:
            logger.info("[IMP:9][preflight][ghcr] ghcr.io auth OK (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail="ghcr.io authentication successful")
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="ghcr.io login failed — context deploy will use build fallback",
            error=result.stderr.strip()[:200],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="ghcr.io probe error — build fallback will be used",
            error=str(e)[:200],
        )


# endregion FUNC_probe_ghcr_auth


# region FUNC_probe_docker_hub
## @purpose — Probe Docker Hub reachability by attempting to pull hello-world manifest.
##            WARN if rate-limited (429) — docker_auth step will configure registry-mirror.
## @io — ⇥ None → ⎋ CheckResult
## @complexity — O(1) + network
## @invariants
##   - WARN: if Docker Hub rate-limited → registry-mirror mitigates
##   - Non-fatal: even if Docker Hub is down, registry-mirror + local cache may suffice
def probe_docker_hub() -> CheckResult:
    """Probe Docker Hub reachability via manifest inspect. WARN on rate-limit."""
    start = time.monotonic()
    try:
        result = subprocess.run(
            ["docker", "manifest", "inspect", "hello-world:latest"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
        latency = int((time.monotonic() - start) * 1000)
        if result.returncode == 0:
            logger.info("[IMP:9][preflight][dockerhub] Docker Hub reachable (%dms)", latency)
            return CheckResult(status="ok", latency_ms=latency, detail="Docker Hub reachable")
        stderr = result.stderr.strip()
        if "429" in stderr or "rate limit" in stderr.lower():
            return CheckResult(
                status="warn",
                latency_ms=latency,
                detail="Docker Hub rate-limited (429) — registry-mirror will be configured",
                error=stderr[:200],
            )
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="Docker Hub manifest inspect failed — registry-mirror will be configured",
            error=stderr[:200],
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="Docker Hub probe error — registry-mirror will be configured",
            error=str(e)[:200],
        )


# endregion FUNC_probe_docker_hub


# region FUNC_probe_dns_resolution
## @purpose — Probe DNS resolution for a domain (platform domain from node.yaml).
##            WARN if DNS fails — cert issuance via DNS-01 will fail.
## @io — ⇥ domain: str → ⎋ CheckResult
## @complexity — O(1)
## @invariants
##   - WARN: if DNS resolution fails → cert issue will fail (non-fatal, nginx can start without HTTPS)
##   - Uses socket.getaddrinfo for resolution
def probe_dns_resolution(domain: str = "") -> CheckResult:
    """Probe DNS resolution for a domain. WARN on failure."""
    start = time.monotonic()
    if not domain:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail="No domain configured — DNS probe skipped",
        )
    try:
        # ⚠️ TRAP[BUG] · 2026-08-03 · P1 · getaddrinfo(timeout=) удалён в Python 3.14 (RC 121 прод)
        # · Symptom: TypeError: getaddrinfo() got an unexpected keyword argument 'timeout'
        # · Fix: socket.setdefaulttimeout(PROBE_TIMEOUT) вокруг вызова (короткая проба, поток preflight)
        socket.setdefaulttimeout(PROBE_TIMEOUT)
        try:
            socket.getaddrinfo(domain, None)
        finally:
            socket.setdefaulttimeout(None)
        latency = int((time.monotonic() - start) * 1000)
        logger.info("[IMP:9][preflight][dns] DNS resolution OK for %s (%dms)", domain, latency)
        return CheckResult(status="ok", latency_ms=latency, detail=f"DNS resolves for {domain}")
    except socket.gaierror as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"DNS resolution failed for {domain} — cert issuance may fail",
            error=str(e)[:200],
        )
    except OSError as e:
        latency = int((time.monotonic() - start) * 1000)
        return CheckResult(
            status="warn",
            latency_ms=latency,
            detail=f"DNS probe error for {domain}",
            error=str(e)[:200],
        )


# endregion FUNC_probe_dns_resolution


# endregion PROBE_FUNCTIONS


# region RUN_ALL_CHECKS


# region FUNC_run_preflight
## @purpose — Run all pre-flight checks and return aggregated result.
##            FATAL checks abort bootstrap; WARN checks are collected.
## @io — ⇥ node_yaml: str, context: str, node_name: str → ⎋ PreflightResult
## @complexity — O(N) where N = number of checks (6 probes)
## @invariants
##   - All checks execute regardless of individual failures (no short-circuit)
##   - FATAL checks are: ssh_connectivity, disk_space
##   - WARN checks are: s3_connectivity, ghcr_auth, docker_hub_probe, dns_resolution
def run_preflight(node_yaml: str = "", context: str = "", node_name: str = "") -> PreflightResult:
    """Run all pre-flight checks, return aggregated result.

    ▶ ┌node.yaml + env┐ → ○ 6 probes in sequence → ◇ classify FATAL/WARN → ⊕ PreflightResult → ⎋
    """
    result = PreflightResult()

    # Extract platform domain from node.yaml for DNS probe
    domain = os.environ.get("PLATFORM_DOMAIN", "")
    if not domain and node_yaml and os.path.isfile(node_yaml):
        domain = _extract_domain_from_node_yaml(node_yaml)

    # Extract S3 credentials from env
    s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "https://s3.timeweb.cloud")
    s3_bucket = os.environ.get("S3_BUCKET", platform_config.default_s3_bucket_sentinel())
    s3_access_key = os.environ.get("S3_ACCESS_KEY", "")
    s3_secret_key = os.environ.get("S3_SECRET_KEY", "")
    ghcr_token = os.environ.get("GHCR_PULL_TOKEN", "")

    logger.info("[IMP:8][preflight] Running pre-flight checks (context=%s, node=%s)", context, node_name)

    # ── FATAL checks ──
    result.add("ssh_connectivity", probe_ssh_connectivity())
    result.add("disk_space", probe_disk_space())

    # ── WARN checks ──
    result.add("s3_connectivity", probe_s3_connectivity(s3_endpoint, s3_bucket, s3_access_key, s3_secret_key))
    result.add("ghcr_auth", probe_ghcr_auth(ghcr_token))
    result.add("docker_hub_probe", probe_docker_hub())
    result.add("dns_resolution", probe_dns_resolution(domain))

    if result.has_fatals():
        logger.error("[IMP:10][preflight] FATAL checks failed: %s", result.fatals)
    else:
        logger.info(
            "[IMP:9][preflight] Pre-flight passed (warnings: %s, fatals: 0)",
            len(result.warnings),
        )
    return result


# endregion FUNC_run_preflight


# region FUNC_extract_domain_from_node_yaml
## @purpose — Extract platform domain from node.yaml for DNS probe.
## @io — ⇥ node_yaml_path: str → ⎋ str (empty if not found)
## @complexity — O(N) for YAML parse
def _extract_domain_from_node_yaml(node_yaml_path: str) -> str:
    """Extract platform domain from node.yaml."""
    try:
        from core.internal.shared.node_yaml import ConfigNotFoundError, ConfigParseError, NodeYaml

        node = NodeYaml(node_yaml_path)
        cfg = node.get_domain_config()
        domain = cfg.platform_domain
        if not domain:
            domain = node.get("node.platform_domain", default="")
            if not domain:
                domain = node.get("node.domain", default="")
        return domain or ""
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.warning("[IMP:7][preflight] Failed to extract domain from %s: %s", node_yaml_path, e)
        return ""


# endregion FUNC_extract_domain_from_node_yaml


# endregion RUN_ALL_CHECKS


# region CLI


# region FUNC_build_parser
## @purpose — Build CLI argument parser for preflight.py.
## @io — ⇥ None → ⎋ argparse.ArgumentParser
## @complexity — O(1)
def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Pre-flight checks for bootstrap-node (DevPlan 047)",
    )
    parser.add_argument("--node-yaml", help="Path to node.yaml")
    parser.add_argument("--context", default="", help="Deployment context name")
    parser.add_argument("--node-name", default="", help="Node name")
    parser.add_argument("--parse-warnings", action="store_true", help="Read JSON from stdin, output warnings to stderr")
    return parser


# endregion FUNC_build_parser


# region FUNC_main
## @purpose — CLI entry point: run pre-flight checks, output JSON, exit 0 (pass) or 1 (fatal).
## @io — ⇥ sys.argv → ⎋ exit code (0 = pass, 1 = fatal)
## @complexity — O(N) where N = number of checks
def main() -> int:
    """Run pre-flight checks and output JSON to stdout. Exit 1 on FATAL."""
    parser = build_parser()
    args = parser.parse_args()

    # --parse-warnings mode: read JSON from stdin, print warnings, exit
    if hasattr(args, "parse_warnings") and args.parse_warnings:
        return _parse_warnings_cli()

    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )

    result = run_preflight(
        node_yaml=args.node_yaml or "",
        context=args.context,
        node_name=args.node_name,
    )

    # Output JSON to stdout (consumed by node-lifecycle.sh)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    if result.has_fatals():
        for fatal in result.fatals:
            check = result.checks[fatal]
            logger.error("[IMP:10][preflight] FATAL: %s — %s", fatal, check.detail)
            if check.error:
                logger.error("[IMP:10][preflight]   error: %s", check.error)
        return 1
    return 0


# endregion FUNC_main


# endregion CLI


# region FUNC_parse_warnings_cli
## @purpose — Parse JSON preflight result from stdin and output warnings to stderr.
##            Used by node-lifecycle.sh to extract warnings from preflight JSON output.
## @io — ⇥ stdin: JSON preflight result → ⎋ None (side-effect: prints warnings to stderr)
## @complexity — O(N) where N = checks
def _parse_warnings_cli() -> int:
    """Read JSON from stdin, output warnings to stderr. Exit 0."""
    import json

    try:
        result = json.load(sys.stdin)
        warnings = [k for k, v in result.items() if v.get("status") == "warn"]
        if warnings:
            print(f"[IMP:7][preflight] Warnings (non-fatal): {warnings}", file=sys.stderr)
    except (json.JSONDecodeError, EOFError):
        pass
    return 0


# endregion FUNC_parse_warnings_cli


if __name__ == "__main__":
    sys.exit(main())
