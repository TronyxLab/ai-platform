#!/usr/bin/env python3
# GREP_SUMMARY: nginx-harness, nginx -t, docker, openssl, dev-certs, vhost-validation, self-signed, docker-ops, docker_image_exists, docker_run_nginx_t
# STRUCTURE: ▶ ┌temp_dir + nginx_version┐ → ◇ docker available? → ⊕ create harness_dir (nginx-main/, includes/, vhosts/, dev-certs/) → ⊕ generate stub nginx.conf + security-headers → ⊕ SSL path swap (re.sub) → ⊕ docker image inspect (shared/docker_ops) → ⊕ docker run nginx -t (shared/docker_ops) → ◇ result? → ⊕ cleanup → ⎋ bool (True = pass, True = skip, False = fail)
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
##   - docker subprocess-вызовы — ТОЛЬКО через shared/docker_ops (гейт docker_sole_path):
##     docker_image_exists (pre-flight inspect) + docker_run_nginx_t (nginx -t)
##   - Cleanup: removes harness_dir after completion
## @rationale Extracted verbatim from vhost_renderer.py (L696-889, ~194 LOC) with all LDD logs,
##            TRAP[DECISION] comment and docstring preserved — no behavior change (AC-G7).
## @changes  2026-08-01 · DevPlan 117 G T53 — extracted from vhost_renderer.py
## @changes  2026-09-01 · P1 (asi-team-vps D-фаза) — NGINX_T_IMAGE digest-pin канон
##            (SoT docker-compose.base.yml:41) вместо незапиненного устаревшего nginx-alpine
##            tag; параметр nginx_version → nginx_image; pre-flight docker image inspect;
##            pull-failure (429/denied/manifest) → non-blocking skip (True), не config-FAIL
## @changes  2026-09-01 · docker-sole-path/C901 — docker image inspect + docker run вызовы
##            → shared/docker_ops (docker_image_exists/docker_run_nginx_t, гейт allowlist пуст);
##            nginx_t_harness декомпозирован (C901 12→6): _create_harness_layout,
##            _copy_dev_vhosts, _stderr_text — поведение не изменено
# endregion MODULE_CONTRACT

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NamedTuple

from core.internal.shared.deploy_paths import letsencrypt_live  # C7/C6: единый резолвер LE-live (118 C7)

# docker-sole-path (гейт allowlist пуст): docker image inspect / docker run nginx -t —
# ЕДИНСТВЕННЫЙ слой shared/docker_ops (docker_image_exists/docker_run_nginx_t, DevPlan 128 W1).
from core.internal.shared.docker_ops import docker_image_exists, docker_run_nginx_t

# DevPlan 118 C11: таймаут docker run nginx -t — канон shared/timeouts.COMPOSE_UP_TIMEOUT
# (литерал 120 удалён; scope гейта timeout_literals расширен на scaffold/).
from core.internal.shared.ssl_certs import DEFAULT_OPENSSL_TIMEOUT  # B5: канон openssl-таймаута
from core.internal.shared.timeouts import COMPOSE_UP_TIMEOUT

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────
# NGINX -T HARNESS
# ──────────────────────────────────────────────────────────────────────

# 🧐 TRAP[DECISION] · 2026-09-01 · — · NGINX_T_IMAGE: digest-pin канон образа nginx для nginx -t harness
# · Rejected: незапиненный nginx-alpine tag (docker.io анонимный pull → 429 Too Many Requests,
#   P1-инцидент deploy-context на ноде asi-team-vps, D-фаза: nginx -t FAIL → render_all abort)
# · Reason: SoT — core/modules/nginx/docker-compose.base.yml:41; образ уже запушен на ноды
#   deploy'ем по digest (анонимный pull не нужен); digest-pin канон (root AGENTS.md DevOps-политика)
# · Rev: если compose сменит digest — обновить здесь (parity-рисок, синхронная правка)
NGINX_T_IMAGE = "nginx:1.30.4-alpine@sha256:8a4f4b94275ff59d809477799cbbaf1a7ab65ed1871403d05e31fd66bdb8db82"

# region FUNC_nginx_t_harness

# 🧐 TRAP[DECISION] · 2026-07-20 · — · nginx_t_harness: isolate overlay vhosts from harness_dir
# · Rejected: storing vhosts and harness support files (security-headers.conf) in same dir
# · Reason: mount -v ${harness_dir}:/etc/nginx/conf.d/overlay:ro exposes ALL .conf files
#   in harness_dir as vhost configs. security-headers.conf is NOT a valid vhost → nginx -t fails
#   with 'unknown directive' on parsed content. Fix: put vhosts in vhosts/ subdir,
#   security-headers in includes/ subdir, dev-certs in dev-certs/ subdir.
# · Rev: if harness nginx.conf is changed to use a non-glob include pattern for vhosts


def nginx_t_harness(temp_dir: str, nginx_image: str = NGINX_T_IMAGE) -> bool:
    """Run nginx -t on rendered configs using docker nginx:alpine image.

    ▶ ┌temp_dir┐ → ◇ docker available? → ⊕ create harness_dir (nginx-main/,
    includes/, vhosts/, dev-certs/) → ⊕ generate stub nginx.conf + security-headers
    → ⊕ SSL path swap (re.sub) → ⊕ docker image inspect (shared/docker_ops pre-flight)
    → ⊕ docker run nginx -t (shared/docker_ops) → ◇ result? → ⊕ cleanup → ⎋ bool (True = pass, False = fail)

    ## @purpose — Validate rendered nginx configs via docker-based nginx -t.
    ##            Replaces 130 lines of shell (mounts, sed, openssl) with Python.
    ##            SSL cert paths are swapped to dev-certs for local validation.
    ##            Falls back to WARN if docker is unavailable.
    ## @io — ⇥ temp_dir: str — directory with rendered vhost .conf files
    ##       ⇥ nginx_image: str — полный image ref nginx:<tag>@sha256:<digest>
    ##            (default: NGINX_T_IMAGE — digest-pin канон из
    ##            core/modules/nginx/docker-compose.base.yml:41, P1 429-фикс)
    ##       → ⎋ bool — True if validation passes or docker unavailable
    ## @complexity — O(V * S) where V = vhost count, S = file size
    ## @invariants
    ##   - Does NOT modify source files — only docker container context
    ##   - Vhosts in vhosts/ subdir, includes in includes/ subdir (isolation)
    ##   - SSL paths /etc/letsencrypt/live/<domain>/ → /etc/nginx/dev-certs/
    ##   - openssl for self-signed certs; fallback to empty files if unavailable
    ##   - Returns True if docker not found (WARN, non-blocking)
    ##   - Returns True if no vhost files to validate
    ##   - Returns True if image pull/daemon failure (429 etc.) — validation non-blocking
    ##   - Cleanup: removes harness_dir after completion
    """
    harness_dir = Path(tempfile.mkdtemp(prefix="nginx_harness_"))
    logger.info("[IMP:7][nginx_t_harness] Starting nginx -t validation (%s)", nginx_image)

    try:
        # ── Step 1-4: Create harness layout (dirs + nginx.conf + security-headers
        #              + dev certs via openssl) ────────────────────────────────
        layout = _create_harness_layout(harness_dir)

        # ── Step 5: Create dev versions of vhosts with SSL paths swapped ──
        vhost_count = _copy_dev_vhosts(temp_dir, layout.vhosts_dir)
        if vhost_count == 0:
            return True

        logger.info("[IMP:7][nginx_t_harness] Validating %d vhost(s) via nginx -t (docker)", vhost_count)

        # Create stub acme directory
        (harness_dir / "acme-stub").mkdir(parents=True, exist_ok=True)

        # ── Step 6: Check docker availability and run nginx -t ────────────
        if shutil.which("docker") is None:
            logger.warning("[IMP:8][nginx_t_harness] docker not available — skipping nginx -t validation (WARN)")
            return True

        # ── Step 6b: Pre-flight — pinned image available locally? ──────────
        # P1 (asi-team-vps, deploy-context D-фаза): docker.io anonymous pull → 429 Too
        # Many Requests на нодах → nginx -t FAIL → render_all abort (all-or-nothing).
        # Канонический digest-pin образ (NGINX_T_IMAGE, SoT docker-compose.base.yml:41)
        # уже запушен на ноды deploy'ем по digest — локальный inspect избегает ненужного pull.
        # Отсутствие образа → WARN и всё равно docker run: pull может быть доступен на
        # dev-машине; на ноде при 429/denied → non-blocking skip ниже (не config-ошибка).
        # docker-sole-path: inspect живёт в shared/docker_ops.docker_image_exists.
        image_present = docker_image_exists(nginx_image, timeout=COMPOSE_UP_TIMEOUT)
        if not image_present:
            logger.warning(
                "[IMP:8][nginx_t_harness] nginx image %s not found locally — attempting pull via docker run",
                nginx_image,
            )

        # docker-sole-path: docker run nginx -t живёт в shared/docker_ops.docker_run_nginx_t.
        docker_result = docker_run_nginx_t(
            mounts=[
                f"{layout.nginx_dir}/nginx.conf:/etc/nginx/nginx.conf:ro",
                f"{layout.dev_certs_dir}:/etc/nginx/dev-certs:ro",
                f"{layout.includes_dir}/security-headers.conf:/etc/nginx/includes/security-headers.conf:ro",
                f"{layout.vhosts_dir}:/etc/nginx/conf.d/overlay:ro",
            ],
            image=nginx_image,
            timeout=COMPOSE_UP_TIMEOUT,
        )

        if docker_result.returncode == 0:
            logger.info("[IMP:9][nginx_t_harness] nginx -t PASS: %d vhost(s) valid", vhost_count)
            return True

        stderr_text = _stderr_text(docker_result)

        # P1: docker.io anonymous pull → 429 / pull access denied / manifest unknown.
        # nginx никогда не стартовал (нет runtime-вывода nginx: [emerg]) → конфиг НЕ
        # валидировался → non-blocking skip (та же WARN-семантика, что docker-unavailable:
        # валидация опциональна по контракту функции). Не silent-fail — явная причина в логе.
        if _is_nginx_image_unavailable(stderr_text):
            logger.error(
                "[IMP:10][nginx_t_harness] nginx image %s недоступен и не найден локально — nginx -t пропущен (WARN, non-blocking)",
                nginx_image,
            )
            first_line = next((ln for ln in stderr_text.splitlines() if ln.strip()), "unknown docker error")
            logger.warning("[IMP:8][nginx_t_harness] image pull failure: %s", first_line)
            return True

        logger.error("[IMP:10][nginx_t_harness] nginx -t FAIL — rendered configs contain syntax errors")
        for line in stderr_text.splitlines():
            logger.error("[IMP:8][nginx_t_harness] nginx -t: %s", line)
        return False

    finally:
        # Cleanup harness directory
        shutil.rmtree(harness_dir, ignore_errors=True)
        logger.debug("[IMP:6][nginx_t_harness] Cleaned up harness dir: %s", harness_dir)


# endregion FUNC_nginx_t_harness


# region DATA_HarnessLayout
class _HarnessLayout(NamedTuple):
    """Пути изолированного harness-контекста (docker-контракт nginx_t_harness).

    ## @purpose — Единый носитель путей 4 поддиректорий harness_dir (nginx-main/,
    ##            vhosts/, includes/, dev-certs/) — возвращается _create_harness_layout.
    ## @io — ⇥ mkdir + файлы → ⎋ NamedTuple(nginx_dir, vhosts_dir, includes_dir, dev_certs_dir)
    """

    nginx_dir: Path
    vhosts_dir: Path
    includes_dir: Path
    dev_certs_dir: Path


# endregion DATA_HarnessLayout


# region FUNC__create_harness_layout
def _create_harness_layout(harness_dir: Path) -> _HarnessLayout:
    """Create harness subdirs + stub nginx.conf + security-headers + dev certs (openssl).

    ▶ ┌harness_dir┐ → ⊕ mkdir 4 subdirs → ⊕ nginx.conf → ⊕ security-headers.conf
    → ⊕ openssl self-signed certs (fallback empty) → ⎋ _HarnessLayout

    ## @purpose — Steps 1-4 of nginx_t_harness: изолированный docker-контекст (nginx-main/,
    ##            vhosts/, includes/, dev-certs/), stub nginx.conf с rate-limit/resolver,
    ##            stub security-headers, self-signed dev certs. openssl subprocess — НЕ docker
    ##            (вне скоупа гейта docker_sole_path; B5 канон DEFAULT_OPENSSL_TIMEOUT).
    ## @io — ⇥ harness_dir: Path → ⎋ _HarnessLayout (4 пути)
    ## @complexity — O(1) + openssl I/O
    ## @invariants — openssl недоступен/fail → пустые cert-файлы (harness продолжает);
    ##               все 4 поддиректории создаются (isolation TRAP[DECISION] 2026-07-20)
    """
    nginx_dir = harness_dir / "nginx-main"
    vhosts_dir = harness_dir / "vhosts"
    includes_dir = harness_dir / "includes"
    dev_certs_dir = harness_dir / "dev-certs"

    nginx_dir.mkdir(parents=True, exist_ok=True)
    vhosts_dir.mkdir(parents=True, exist_ok=True)
    includes_dir.mkdir(parents=True, exist_ok=True)
    dev_certs_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 2: Generate minimal nginx.conf ───────────────────────────
    nginx_conf = nginx_dir / "nginx.conf"
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
            timeout=DEFAULT_OPENSSL_TIMEOUT,  # B5: канон openssl-таймаута (литерал 30 удалён)
            check=False,
        )
        if result.returncode != 0:
            logger.warning("[IMP:8][_create_harness_layout] openssl failed — creating empty cert files")
            fullchain.write_text("", encoding="utf-8")
            privkey.write_text("", encoding="utf-8")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("[IMP:8][_create_harness_layout] openssl not available — creating empty cert files")
        fullchain.write_text("", encoding="utf-8")
        privkey.write_text("", encoding="utf-8")

    return _HarnessLayout(
        nginx_dir=nginx_dir,
        vhosts_dir=vhosts_dir,
        includes_dir=includes_dir,
        dev_certs_dir=dev_certs_dir,
    )


# endregion FUNC__create_harness_layout


# region FUNC__copy_dev_vhosts
def _copy_dev_vhosts(temp_dir: str, vhosts_dir: Path) -> int:
    """Copy vhost .conf files to harness vhosts/ with SSL paths swapped to dev-certs.

    ▶ ┌temp_dir, vhosts_dir┐ → ○ glob *.conf → ○ for each: re.sub LE-live paths → dev-certs
    → ⊕ write dev_vhost → ⎋ count (0 = нет vhost'ов → SKIP)

    ## @purpose — Step 5 of nginx_t_harness: dev-версии vhost'ов (SSL paths → dev-certs,
    ##            acme root → stub). Возвращает 0 при отсутствии .conf файлов — caller
    ##            трактует как SKIP (non-blocking контракт функции).
    ## @io — ⇥ temp_dir: str, vhosts_dir: Path → ⎋ int (число скопированных vhost'ов)
    ## @complexity — O(V * S) — V vhost'ов × размер файла
    ## @invariants — LE-live пути через letsencrypt_live() (C6/RC 121); acme → /tmp/acme-stub
    ##               (nosec B108 — dev-only); только файлы (не директории)
    """
    temp_path = Path(temp_dir)
    vhost_files = list(temp_path.glob("*.conf"))

    if not vhost_files:
        logger.info("[IMP:7][_copy_dev_vhosts] No vhost files to validate — SKIP")
        return 0

    # C6 (DevPlan 119, закрыт RC 121): /etc/letsencrypt/live хардкод → letsencrypt_live()
    # (shared/deploy_paths, 118 C7). Prod-дефолт не меняется (env LETSENCRYPT_LIVE не задан).
    le_live = re.escape(str(letsencrypt_live()))
    vhost_count = 0
    for vhost_file in vhost_files:
        if not vhost_file.is_file():
            continue
        dev_vhost = vhosts_dir / vhost_file.name
        content = vhost_file.read_text(encoding="utf-8")
        # Replace production SSL paths with dev-certs for validation
        swapped = re.sub(
            le_live + r"/[^/]*/fullchain\.pem",
            "/etc/nginx/dev-certs/fullchain.pem",
            content,
        )
        swapped = re.sub(
            le_live + r"/[^/]*/privkey\.pem",
            "/etc/nginx/dev-certs/privkey.pem",
            swapped,
        )
        swapped = swapped.replace("/var/www/acme", "/tmp/acme-stub")  # nosec B108 — dev-only acme stub, not production
        dev_vhost.write_text(swapped, encoding="utf-8")
        vhost_count += 1

    return vhost_count


# endregion FUNC__copy_dev_vhosts


# region FUNC__stderr_text
def _stderr_text(result: subprocess.CompletedProcess[str]) -> str:
    """Normalize CompletedProcess.stderr to str (bytes → decode, TRAP[BUG] type-safety).

    ▶ ┌result┐ → ◇ stderr bytes? → decode utf-8 → ⎋ str

    ## @purpose — docker_run_nginx_t (docker_ops, text=True) возвращает str stderr; тесты/
    ##            моки могут давать bytes — единая нормализация перед маркер-сканированием
    ##            _is_nginx_image_unavailable (TRAP[BUG] type-safety, зеркало docker_ops._stdout_str).
    ## @io — ⇥ result: subprocess.CompletedProcess[str] → ⎋ str
    ## @complexity — O(1)
    """
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="replace")
    return stderr


# endregion FUNC__stderr_text


# region FUNC__is_nginx_image_unavailable


def _is_nginx_image_unavailable(stderr_text: str) -> bool:
    """True when docker run failed BEFORE nginx started (image pull/daemon error).

    ▶ ◇ pull-markers in stderr? → True → ⎋ bool

    ## @purpose — Distinguish image-unavailability (429 rate-limit / pull access denied /
    ##            manifest unknown / daemon down) from a REAL config syntax error.
    ##            P1 asi-team-vps: docker.io anonymous pull → 429 on nodes — nginx never
    ##            starts, the rendered config was NOT validated → non-blocking skip, not FAIL.
    ## @io — ⇥ stderr_text: str — stderr of `docker run ... nginx -t`
    ##       → ⎋ bool — True = image/pull/daemon failure (not a config error)
    ## @complexity — O(1) — fixed set of markers
    ## @invariants — False for `nginx: [emerg] ...` runtime output (real config error)
    """
    pull_markers = (
        "toomanyrequests",  # docker.io 429 Too Many Requests (P1 root cause)
        "Too Many Requests",
        "pull access denied",
        "manifest unknown",
        "Unable to find image",
        "pull rate limit",
        "no matching manifest",
        "Cannot connect to the Docker daemon",
    )
    return any(marker in stderr_text for marker in pull_markers)


# endregion FUNC__is_nginx_image_unavailable
