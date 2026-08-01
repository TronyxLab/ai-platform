#!/usr/bin/env python3
# GREP_SUMMARY: nginx-harness, nginx -t, docker, openssl, dev-certs, vhost-validation, self-signed
# STRUCTURE: ▶ ┌temp_dir + nginx_version┐ → ◇ docker available? → ⊕ create harness_dir (nginx-main/, includes/, vhosts/, dev-certs/) → ⊕ generate stub nginx.conf + security-headers → ⊕ SSL path swap (re.sub) → ⊕ docker run nginx -t → ◇ result? → ⊕ cleanup → ⎋ bool (True = pass, True = skip, False = fail)
# region MODULE_CONTRACT
## @purpose  Standalone nginx -t validation harness extracted from vhost_renderer.py (DevPlan 117 G T53).
##           Creates an isolated Docker context (harness_dir), swaps production SSL paths to dev-certs,
##           and runs `docker run nginx -t` over rendered vhost configs. Graceful non-blocking fallback
##           when docker is unavailable or no vhost files are present.
## @scope    Consumed by vhost_renderer.py::render_all() (lazy import) and the vhost CLI. Never imported
##           at module level by consumers (start-up time invariant, AC-G5).
## @invariants
##   - Does NOT modify source files — only docker container context
##   - Vhosts in vhosts/ subdir, includes in includes/ subdir (isolation)
##   - SSL paths /etc/letsencrypt/live/<domain>/ → /etc/nginx/dev-certs/
##   - openssl for self-signed certs; fallback to empty files if unavailable
##   - Returns True if docker not found (WARN, non-blocking)
##   - Returns True if no vhost files to validate
##   - Cleanup: removes harness_dir after completion
## @rationale Extracted verbatim from vhost_renderer.py (L696-889, ~194 LOC) with all LDD logs,
##            TRAP[DECISION] comment and docstring preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T53 — extracted from vhost_renderer.py
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# NGINX -T HARNESS
# ──────────────────────────────────────────────────────────────────────

# region FUNC_nginx_t_harness

# 🧐 TRAP[DECISION] · 2026-07-20 · — · nginx_t_harness: isolate overlay vhosts from harness_dir
# · Rejected: storing vhosts and harness support files (security-headers.conf) in same dir
# · Reason: mount -v ${harness_dir}:/etc/nginx/conf.d/overlay:ro exposes ALL .conf files
#   in harness_dir as vhost configs. security-headers.conf is NOT a valid vhost → nginx -t fails
#   with 'unknown directive' on parsed content. Fix: put vhosts in vhosts/ subdir,
#   security-headers in includes/ subdir, dev-certs in dev-certs/ subdir.
# · Rev: if harness nginx.conf is changed to use a non-glob include pattern for vhosts


def nginx_t_harness(temp_dir: str, nginx_version: str = "1.28-alpine") -> bool:
    """Run nginx -t on rendered configs using docker nginx:alpine image.

    ▶ ┌temp_dir┐ → ◇ docker available? → ⊕ create harness_dir (nginx-main/,
    includes/, vhosts/, dev-certs/) → ⊕ generate stub nginx.conf + security-headers
    → ⊕ SSL path swap (re.sub) → ⊕ docker run nginx -t → ◇ result?
    → ⊕ cleanup → ⎋ bool (True = pass, False = fail)

    ## @purpose — Validate rendered nginx configs via docker-based nginx -t.
    ##            Replaces 130 lines of shell (mounts, sed, openssl) with Python.
    ##            SSL cert paths are swapped to dev-certs for local validation.
    ##            Falls back to WARN if docker is unavailable.
    ## @io — ⇥ temp_dir: str — directory with rendered vhost .conf files
    ##       ⇥ nginx_version: str — nginx image tag (default: 1.28-alpine)
    ##       → ⎋ bool — True if validation passes or docker unavailable
    ## @complexity — O(V * S) where V = vhost count, S = file size
    ## @invariants
    ##   - Does NOT modify source files — only docker container context
    ##   - Vhosts in vhosts/ subdir, includes in includes/ subdir (isolation)
    ##   - SSL paths /etc/letsencrypt/live/<domain>/ → /etc/nginx/dev-certs/
    ##   - openssl for self-signed certs; fallback to empty files if unavailable
    ##   - Returns True if docker not found (WARN, non-blocking)
    ##   - Returns True if no vhost files to validate
    ##   - Cleanup: removes harness_dir after completion
    """
    harness_dir = Path(tempfile.mkdtemp(prefix="nginx_harness_"))
    logger.info("[IMP:7][nginx_t_harness] Starting nginx -t validation (nginx:%s)", nginx_version)

    try:
        # ── Step 1: Create harness subdirectory structure ─────────────────
        # nginx-main/ — contains minimal nginx.conf for validation
        # vhosts/ — contains dev-certs-swapped vhost files
        # includes/ — contains stub security-headers.conf
        # dev-certs/ — contains self-signed dev certificates
        harness_nginx_dir = harness_dir / "nginx-main"
        vhosts_dir = harness_dir / "vhosts"
        includes_dir = harness_dir / "includes"
        dev_certs_dir = harness_dir / "dev-certs"

        harness_nginx_dir.mkdir(parents=True, exist_ok=True)
        vhosts_dir.mkdir(parents=True, exist_ok=True)
        includes_dir.mkdir(parents=True, exist_ok=True)
        dev_certs_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 2: Generate minimal nginx.conf ───────────────────────────
        nginx_conf = harness_nginx_dir / "nginx.conf"
        nginx_conf.write_text(
            """events {
    worker_connections 64;
}
http {
    # Base includes for vhosts to resolve
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # Rate limiting zones (referenced by GENERATED vhosts)
    limit_req_zone $binary_remote_addr zone=dynamic:10m rate=10r/s;

    # Docker DNS resolver
    resolver 127.0.0.11 valid=30s ipv6=off;

    # Stub security headers include
    server {
        listen 80 default_server;
        return 444;
    }

    # Overlay vhosts
    include /etc/nginx/conf.d/overlay/*.conf;
}
""",
            encoding="utf-8",
        )

        # ── Step 3: Generate stub security-headers.conf ───────────────────
        security_headers = includes_dir / "security-headers.conf"
        security_headers.write_text(
            """# Stub security-headers.conf for nginx -t validation
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
""",
            encoding="utf-8",
        )

        # ── Step 4: Generate dev certs ────────────────────────────────────
        fullchain = dev_certs_dir / "fullchain.pem"
        privkey = dev_certs_dir / "privkey.pem"

        try:
            result = subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-nodes",
                    "-days",
                    "1",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    str(privkey),
                    "-out",
                    str(fullchain),
                    "-subj",
                    "/CN=localhost",
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning("[IMP:8][nginx_t_harness] openssl failed — creating empty cert files")
                fullchain.write_text("", encoding="utf-8")
                privkey.write_text("", encoding="utf-8")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.warning("[IMP:8][nginx_t_harness] openssl not available — creating empty cert files")
            fullchain.write_text("", encoding="utf-8")
            privkey.write_text("", encoding="utf-8")

        # ── Step 5: Create dev versions of vhosts with SSL paths swapped ──
        temp_path = Path(temp_dir)
        vhost_files = list(temp_path.glob("*.conf"))

        if not vhost_files:
            logger.info("[IMP:7][nginx_t_harness] No vhost files to validate — SKIP")
            return True

        vhost_count = 0
        for vhost_file in vhost_files:
            if not vhost_file.is_file():
                continue
            dev_vhost = vhosts_dir / vhost_file.name
            content = vhost_file.read_text(encoding="utf-8")
            # Replace production SSL paths with dev-certs for validation
            swapped = re.sub(
                r"/etc/letsencrypt/live/[^/]*/fullchain\.pem",
                "/etc/nginx/dev-certs/fullchain.pem",
                content,
            )
            swapped = re.sub(
                r"/etc/letsencrypt/live/[^/]*/privkey\.pem",
                "/etc/nginx/dev-certs/privkey.pem",
                swapped,
            )
            swapped = swapped.replace("/var/www/acme", "/tmp/acme-stub")  # nosec B108 — dev-only acme stub, not production
            dev_vhost.write_text(swapped, encoding="utf-8")
            vhost_count += 1

        logger.info("[IMP:7][nginx_t_harness] Validating %d vhost(s) via nginx -t (docker)", vhost_count)

        # Create stub acme directory
        acme_stub = harness_dir / "acme-stub"
        acme_stub.mkdir(parents=True, exist_ok=True)

        # ── Step 6: Check docker availability and run nginx -t ────────────
        if shutil.which("docker") is None:
            logger.warning("[IMP:8][nginx_t_harness] docker not available — skipping nginx -t validation (WARN)")
            return True

        docker_result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{harness_nginx_dir}/nginx.conf:/etc/nginx/nginx.conf:ro",
                "-v",
                f"{dev_certs_dir}:/etc/nginx/dev-certs:ro",
                "-v",
                f"{includes_dir}/security-headers.conf:/etc/nginx/includes/security-headers.conf:ro",
                "-v",
                f"{vhosts_dir}:/etc/nginx/conf.d/overlay:ro",
                f"nginx:{nginx_version}",
                "nginx",
                "-t",
            ],
            capture_output=True,
            timeout=120,
        )

        if docker_result.returncode == 0:
            logger.info("[IMP:9][nginx_t_harness] nginx -t PASS: %d vhost(s) valid", vhost_count)
            return True
        logger.error("[IMP:10][nginx_t_harness] nginx -t FAIL — rendered configs contain syntax errors")
        stderr_text = docker_result.stderr.decode("utf-8", errors="replace")
        for line in stderr_text.splitlines():
            logger.error("[IMP:8][nginx_t_harness] nginx -t: %s", line)
        return False

    finally:
        # Cleanup harness directory
        shutil.rmtree(harness_dir, ignore_errors=True)
        logger.debug("[IMP:6][nginx_t_harness] Cleaned up harness dir: %s", harness_dir)


# endregion FUNC_nginx_t_harness
