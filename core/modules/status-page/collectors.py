#!/usr/bin/env python3
# GREP_SUMMARY: status-page collectors load-node-yaml load-status-metrics vhosts modules curl-vhost curl-platform-service check-container staleness get-all-checks
# STRUCTURE: ▶ load_node_yaml → ▶ _load_status_metrics (schema ≥2) → ▶ get_vhosts (expose:true) → ▶ get_modules
#            → ▶ _curl_vhost (--resolve via nginx) → ▶ _curl_platform_service (Docker DNS) → ▶ _check_container
#            → ▶ _compute_staleness (>5min) → ▶ get_all_checks (parallel fan-out, aggregate) → ⎋ dict
# region MODULE_CONTRACT
## @purpose  Data collectors for the status-page module, extracted from app.py (DevPlan 117 G T55).
##           Pure functions — all configuration (paths, timeouts, PLATFORM_SERVICES) is passed as
##           parameters by the orchestrator (app.py). No module-level env coupling → fully testable.
## @scope    Consumed by core/modules/status-page/app.py (module-level import — Docker container,
##           start-up time not critical). Runs inside the status-page container.
## @invariants
##   - NO core/internal imports (cross-layer violation forbidden for modules)
##   - Total check timeout ≤30s (per-check timeout ≤5s)
##   - Anti-recursion: excludes status-page container from self-checks
##   - _load_status_metrics: checks schema_version ≥ 2 (warning, still returns data)
##   - _curl_vhost: --resolve bypasses Docker embedded DNS; 200/401/403 = PASS
##   - _curl_platform_service: accepts 200-399/401/403 as PASS
##   - _compute_staleness: >5 min → "Xm Ys" description, else None
## @rationale  DevPlan 117 G T55 — extracted verbatim from app.py collectors (~450 LOC) with all
##            LDD logs, TRAP comments and docstrings preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T55 — extracted from app.py
# endregion MODULE_CONTRACT

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════════


# region FUNC_load_node_yaml
def load_node_yaml(path: str) -> dict:
    """Load and parse node.yaml. Returns empty dict on failure."""
    try:
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return data if data else {}
    except Exception as e:
        print(f"[IMP:8][status-page][load-yaml] Failed to load node.yaml: {e}", file=sys.stderr)
        return {}


# endregion FUNC_load_node_yaml


# region FUNC_load_status_metrics
def load_status_metrics(path: str) -> dict:
    """Load status-metrics.json with schema_version check.

    # ▶ ┌path┐ → open JSON → ◇ schema_version >= 2? → return data
    #                                          └→ log warning, return empty
    # On failure → return empty containers/certs/projects/host with errors[]

    Returns full data dict as-is from file, or fallback structure on failure.
    """
    # Protective: Docker bind mount может создать path как директорию (P1)
    if not os.path.isfile(path):
        print(f"[IMP:8][status-page][load-metrics] Path is not a file: {path}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": [f"status-metrics.json not found or is a directory at {path}"],
        }

    try:
        with open(path) as f:
            data = json.load(f)

        # Schema version check
        sv = data.get("schema_version", 0)
        if sv < 2:
            print(f"[IMP:8][status-page][load-metrics] WARNING: schema_version={sv}, expected >=2", file=sys.stderr)
            # Still return data — older schema is partially compatible

        return data
    except Exception as e:
        print(f"[IMP:8][status-page][load-metrics] Failed to load status-metrics.json: {e}", file=sys.stderr)
        return {
            "generated_at": None,
            "containers": [],
            "certs": [],
            "projects": [],
            "host": {},
            "errors": ["Failed to load status-metrics.json"],
        }


# endregion FUNC_load_status_metrics


# region FUNC_get_vhosts
def get_vhosts(node_data: dict) -> list[dict]:
    """Extract expose:true domains from node.yaml projects list."""
    projects = node_data.get("projects", [])
    vhosts = []
    for p in projects:
        if isinstance(p, dict) and p.get("expose", False) is True:
            domain = p.get("domain", "")
            if domain:
                vhosts.append(
                    {
                        "domain": domain,
                        "name": p.get("name", domain),
                        "repo_url": p.get("repo_url", ""),
                    }
                )
    return vhosts


# endregion FUNC_get_vhosts


# region FUNC_get_modules
def get_modules(node_data: dict) -> list[str]:
    """Get list of deployed module names from node.yaml."""
    return node_data.get("modules", [])


# endregion FUNC_get_modules


# ═══════════════════════════════════════════════════════════════════
# CHECK LAYER
# ═══════════════════════════════════════════════════════════════════


# region FUNC_curl_vhost
def _curl_vhost(domain: str, timeout: int = 5) -> dict:
    """Live-curl a vhost domain. Returns check result dict."""
    start = time.monotonic()
    try:
        # --resolve bypasses Docker embedded DNS (127.0.0.11) which resolves *.tronyx.ru → localhost (P4)
        result = subprocess.run(
            [
                "curl",
                "-sSk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                str(timeout),
                "--resolve",
                f"{domain}:443:nginx",
                f"https://{domain}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_code = result.stdout.strip()
        if result.returncode == 0 and http_code.isdigit():
            code = int(http_code)
            # HTTP 200 = explicit success
            # HTTP 401/403 = auth required — service IS alive and responding,
            #   just needs credentials. Treat as PASS (not WARN/FAIL).
            return {
                "target": domain,
                "type": "vhost",
                "status": "PASS" if (code == 200 or code in (401, 403)) else "WARN",
                "http_code": code,
                "duration_ms": elapsed_ms,
                "error": None,
            }
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": int(http_code) if http_code.isdigit() else 0,
            "duration_ms": elapsed_ms,
            "error": f"curl exit {result.returncode}: {result.stderr.strip()}",
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": f"timeout after {timeout}s",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "target": domain,
            "type": "vhost",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": str(e),
        }


# endregion FUNC_curl_vhost


# region FUNC_curl_platform_service
def _curl_platform_service(internal_url: str, health_path: str, timeout: int = 5) -> dict:
    """Live-curl a platform service via Docker internal DNS.

    # ▶ ┌internal_url (e.g. "grafana:3000") + health_path ("/api/health")┐
    #    → subprocess.run curl (без --resolve, через Docker DNS)
    #    → ⎋ check result dict: {target, type, status, duration_ms, error}

    Unlike _curl_vhost, this does NOT use --resolve — Docker DNS resolves
    internal service names directly (grafana:3000 → container IP).
    Timeout: 5s per check.
    """
    start = time.monotonic()
    url = f"http://{internal_url}{health_path}"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sSk",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                str(timeout),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)
        http_code = result.stdout.strip()
        # Extract hostname for the target field
        target_host = internal_url.split(":")[0]

        if result.returncode == 0 and http_code.isdigit():
            code = int(http_code)
            # Accept 200-399 as PASS (some services return 302, 301, etc.)
            # HTTP 401/403 = auth required — service IS alive and responding,
            #   just needs credentials. Treat as PASS (not WARN/FAIL).
            status = "PASS" if (200 <= code < 400 or code in (401, 403)) else "WARN"
            return {
                "target": target_host,
                "type": "platform_service",
                "status": status,
                "http_code": code,
                "duration_ms": elapsed_ms,
                "error": None,
            }
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": int(http_code) if http_code.isdigit() else 0,
            "duration_ms": elapsed_ms,
            "error": f"curl exit {result.returncode}: {result.stderr.strip()[:100]}",
        }
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        target_host = internal_url.split(":")[0]
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": f"timeout after {timeout}s",
        }
    except Exception as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        target_host = internal_url.split(":")[0]
        return {
            "target": target_host,
            "type": "platform_service",
            "status": "FAIL",
            "http_code": 0,
            "duration_ms": elapsed_ms,
            "error": str(e),
        }


# endregion FUNC_curl_platform_service


# region FUNC_check_container
def _check_container(container: dict) -> dict | None:
    """Check a single container from status-metrics.json data. Returns check result.

    Status logic:
    - Running & healthy → PASS
    - Running & not healthy → WARN
    - Not running, exit_code=0 OR status_line contains "Exited (0)" → PASS (oneshot/init completed)
    - Not running, exit_code>0 OR status_line contains "Exited (non-zero)" → FAIL
    - Other non-running → FAIL
    """
    name = container.get("name", "unknown")  # Δ8: container_name → name
    running = container.get("running", False)
    healthy = container.get("healthy", False)
    exit_code = container.get("exit_code")
    status_line = container.get("status_line", "")

    # Anti-recursion: skip self
    if name == "status-page":
        return None

    if running and healthy:
        check_status = "PASS"
    elif running and not healthy:
        check_status = "WARN"
    elif not running:
        # Determine exit code: prefer explicit field, fall back to parsing status_line
        if exit_code is None:
            m = re.search(r"Exited\s*\((\d+)\)", status_line)
            exit_code = int(m.group(1)) if m else None
        # Oneshot/init container that completed successfully → PASS, otherwise FAIL
        check_status = "PASS" if (exit_code is not None and exit_code == 0) else "FAIL"
    else:
        check_status = "FAIL"

    return {
        "target": name,
        "type": "container",
        "status": check_status,
        "running": running,
        "healthy": healthy,
        "exit_code": exit_code,
        "status_line": status_line,
        "error": None if check_status == "PASS" else f"status: {status_line}",
    }


# endregion FUNC_check_container


# region FUNC_compute_staleness
def compute_staleness(generated_at: str | None) -> str | None:
    """Compute staleness of metrics data. Returns None if fresh, string description if stale.

    # ▶ ┌generated_at (ISO 8601)┐ → ◇ None? → ⎋ None
    #                               → ◇ age > 5 min? → ⎋ "Xm Ys" description
    #                               → ⎋ None (fresh)
    """
    if not generated_at:
        return None

    try:
        if generated_at.endswith("Z"):
            generated_at = generated_at[:-1] + "+00:00"
        gen_time = datetime.fromisoformat(generated_at)
        now = datetime.now(timezone.utc)
        delta = now - gen_time
        if delta.total_seconds() > 300:  # 5 minutes
            minutes = int(delta.total_seconds() // 60)
            seconds = int(delta.total_seconds() % 60)
            return f"{minutes}m {seconds}s"
    except (ValueError, TypeError):
        pass

    return None


# endregion FUNC_compute_staleness


# region FUNC_get_all_checks
def get_all_checks(
    node_yaml_path: str,
    status_metrics_json: str,
    platform_services: list[dict],
    per_check_timeout: int = 5,
    total_timeout: int = 30,
) -> dict:
    """Run all checks (vhosts + containers from metrics) with parallel fan-out. Returns aggregate dict."""
    start = time.monotonic()
    checks = []

    node_data = load_node_yaml(node_yaml_path)
    metrics = load_status_metrics(status_metrics_json)
    freshness = metrics.get("generated_at")

    # ── Container checks (from status-metrics.json containers) ──
    containers = metrics.get("containers", [])
    for c in containers:
        result = _check_container(c)
        if result is not None:
            checks.append(result)

    # ── Vhost checks (live curl, parallel fan-out) ──
    vhosts = get_vhosts(node_data)
    if vhosts:
        with ThreadPoolExecutor(max_workers=min(len(vhosts), 10)) as executor:
            futures = {executor.submit(_curl_vhost, v["domain"], per_check_timeout): v for v in vhosts}
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=total_timeout)
                    checks.append(result)
                except Exception as e:  # noqa: PERF203
                    v = futures[future]
                    checks.append(
                        {
                            "target": v["domain"],
                            "type": "vhost",
                            "status": "FAIL",
                            "http_code": 0,
                            "duration_ms": 0,
                            "error": f"future timeout: {e}",
                        }
                    )

    # ── Platform service checks (live curl via Docker DNS, parallel fan-out) ──
    if platform_services:
        with ThreadPoolExecutor(max_workers=min(len(platform_services), 10)) as executor:
            futures = {
                executor.submit(_curl_platform_service, svc["internal"], svc["health_path"], per_check_timeout): svc
                for svc in platform_services
            }
            for future in as_completed(futures):
                try:
                    result = future.result(timeout=total_timeout)
                    checks.append(result)
                except Exception as e:  # noqa: PERF203
                    svc = futures[future]
                    checks.append(
                        {
                            "target": svc["internal"].split(":")[0],
                            "type": "platform_service",
                            "status": "FAIL",
                            "http_code": 0,
                            "duration_ms": 0,
                            "error": f"future timeout: {e}",
                        }
                    )

    # ── Compute aggregate status ──
    all_pass = all(c["status"] == "PASS" for c in checks)
    duration_ms = int((time.monotonic() - start) * 1000)

    # Check staleness
    staleness = compute_staleness(freshness)
    # If stale, overall status is still WARN (not FAIL) — data exists but may be old
    overall = "PASS" if all_pass else "FAIL"

    return {
        "status": overall,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": duration_ms,
        "metrics_freshness": freshness,
        "staleness": staleness,
        "checks": checks,
        # Full metrics data for HTML template
        "metrics": metrics,
    }


# endregion FUNC_get_all_checks
