#!/usr/bin/env python3
# pyright: reportImplicitRelativeImport=false
# GREP_SUMMARY: status-page app.py live-status http.server node.yaml status-metrics.json jinja2 html json health refresh orchestrator collectors renderer disabled node-name metrics prometheus tls
# STRUCTURE: ▶ Config → ▶ _jinja_env (once) → ▶ wrappers (get_all_checks/_render_html) → ▶ StatusPageHandler(do_GET|do_POST)
#            → /: Jinja2 render → /health: binary verdict → /healthz: readiness_check → /status.json: JSON → /refresh: POST → ▶ main
# region MODULE_CONTRACT
## @purpose  Live status-page HTTP server (thin orchestrator, 117 G T55 + 170 W7-E2).
##           Бизнес-логика — в collectors-пакете (данные/чеки) и renderer-пакете (HTML).
## @scope    Внутри status-page Docker container (8080, internal-only); nginx proxy_pass + Basic Auth.
## @invariants
##   - 5 маршрутов + статус-коды + JSON-поля сохранены (domain_verifier ждёт status/checks_count/
##     failed_count); /health: 200 PASS | 503 FAIL (AC3-M); /healthz — readiness_check (stale → 503)
##   - Jinja2 autoescape (XSS, Δ12); Environment — один раз на модуль; ThreadingHTTPServer (M-1)
##   - Timeout-бюджеты и anti-recursion — в collectors-пакете
## @rationale  W7-E2: маршруты + вызовы; приватные имена тестов сохранены (get_all_checks,
##            _render_html, PLATFORM_SERVICES). NODE_YAML_PATH — config-константы ниже.
## @changes  2026-08-15 · 170 W7-E2 | collectors.py/renderer.py → пакеты; _handle_healthz → readiness_check
# ⚠️ TRAP[DECISION] · 2026-08-01 · — · status-page stays on raw yaml.safe_load — exception from NodeYaml facade
# · Rejected: core.internal.shared.node_yaml (layer violation + image bloat); modules→internal import forbidden
# · Rev: if node.yaml reading moves into a core-owned service → remove raw parsing here.
# endregion MODULE_CONTRACT

import http.server
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import cast

from collectors import (
    PLATFORM_SERVICES,
)
from collectors import (
    extract_node_name as _extract_node_name,
)
from collectors import (
    get_all_checks as _get_all_checks_impl,
)
from collectors import (
    load_node_yaml as _load_node_yaml,
)
from collectors import (
    readiness_check as _readiness_check,
)
from collectors.aggregate import OverallData
from collectors.checks.platform import PlatformService
from collectors.config import MetricsData
from jinja2 import Environment, FileSystemLoader, select_autoescape
from renderer import render_html as _render_html_impl

# ═══════════════ CONFIGURATION ═══════════════
LISTEN_PORT = int(os.environ.get("STATUS_PAGE_PORT", "8080"))
# AI-0072 (DevPlan 17 T3.6): env-ручка STATUS_PAGE_HOST удалена — никто её не задавал;
# контейнер не публикует порты (ingress через nginx proxy_pass) → bind 0.0.0.0 внутри сети
LISTEN_HOST = "0.0.0.0"
NODE_NAME = os.environ.get("NODE_NAME", "test-node")
NODE_CONFIGS_DIR = os.environ.get("NODE_CONFIGS_DIR", "/opt/node-configs")
# 142 W2 (B21): прод-дефолт — persistent /var/lib/platform/run (переживает reboot).
# ⚠️ TRAP[DECISION] · 2026-08-14 · — · Дубль deploy_paths.status_metrics_json — cross-layer
# · Rejected: импорт core/internal/shared/deploy_paths (modules НЕ импортируют core/internal)
# · Rev: если появится модульный механизм инъекции путей (env/compose) → убрать дубль.
STATUS_METRICS_PATH: str = "/var/lib/platform/run/status-metrics.json"
STATUS_METRICS_JSON = os.environ.get("STATUS_METRICS_JSON", STATUS_METRICS_PATH)
PER_CHECK_TIMEOUT = int(os.environ.get("PER_CHECK_TIMEOUT", "5"))
TOTAL_TIMEOUT = int(os.environ.get("TOTAL_TIMEOUT", "30"))
NODE_YAML_PATH = os.path.join(NODE_CONFIGS_DIR, NODE_NAME, "node.yaml")

# ═══════════════ JINJA2 (once at module level) ═══════════════
_jinja_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=select_autoescape(["html"]),
)


# ═══════════════ ORCHESTRATOR WRAPPERS ═══════════════
# region FUNC_get_all_checks
def get_all_checks(
    *,
    node_yaml_path: str | None = None,
    status_metrics_json: str | None = None,
    platform_services: list[PlatformService] | None = None,
    per_check_timeout: int | None = None,
    total_timeout: int | None = None,
) -> OverallData:
    return _get_all_checks_impl(
        node_yaml_path=node_yaml_path or NODE_YAML_PATH,
        status_metrics_json=status_metrics_json or STATUS_METRICS_JSON,
        platform_services=platform_services if platform_services is not None else PLATFORM_SERVICES,
        per_check_timeout=per_check_timeout or PER_CHECK_TIMEOUT,
        total_timeout=total_timeout or TOTAL_TIMEOUT,
    )


# endregion FUNC_get_all_checks


# region FUNC_render_html
def _render_html(data: OverallData) -> str:
    node_name = _extract_node_name(_load_node_yaml(NODE_YAML_PATH), fallback=NODE_NAME)
    # W11: OverallData (TypedDict) → dict[str, object] через object-мост (renderer-граница)
    return _render_html_impl(
        cast("dict[str, object]", cast(object, data)),
        _jinja_env,
        platform_services=PLATFORM_SERVICES,
        node_name=node_name,
    )


# endregion FUNC_render_html


# ═══════════════ HTTP HANDLER ═══════════════
# region CLASS_StatusPageHandler
class StatusPageHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, content_type: str, body: bytes, status_code: int, freshness: str | None = None):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        if freshness:
            self.send_header("X-Data-Freshness", freshness)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: dict[str, object], status_code: int = 200):
        self._send(
            "application/json",
            json.dumps(data, indent=2).encode("utf-8"),
            status_code,
            cast("str | None", data.get("metrics_freshness")),
        )

    def _send_html(self, html: str, freshness: str | None = None):
        self._send("text/html; charset=utf-8", html.encode("utf-8"), 200, freshness)

    def do_GET(self):
        try:  # ruff: ignore[PLW0717] — router-try: единая HTTP-граница, извлечение ломает catch-all
            path = self.path.rstrip("/") or "/"
            if path == "/health":
                self._handle_health()
            elif path == "/healthz":
                self._handle_healthz()
            elif path == "/status.json":
                self._handle_status_json()
            elif path == "/metrics":
                self._handle_metrics()
            else:
                self._handle_html()
        except Exception as e:  # noqa: EXC — HTTP boundary — сбой обработчика → 500, сервер жив
            print(f"[IMP:8][status-page][handler] Error handling {self.path}: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"ERROR","error":"internal server error"}')

    def do_POST(self):
        try:  # ruff: ignore[PLW0717] — router-try: единая HTTP-граница
            path = self.path.rstrip("/") or "/"
            if path == "/refresh":
                self._handle_refresh()
            else:
                self.send_response(405)
                self.end_headers()
                self.wfile.write(b"Method Not Allowed")
        except Exception as e:  # noqa: EXC — HTTP boundary
            print(f"[IMP:8][status-page][handler] POST error: {e}", file=sys.stderr)
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"status":"ERROR"}')

    def _handle_health(self):
        data = get_all_checks()
        checks = data.get("checks") or []
        duration_ms = data.get("duration_ms", 0)
        if data.get("status") == "PASS" and not data.get("staleness"):
            self._send_json({"status": "PASS", "checks_count": len(checks), "duration_ms": duration_ms})
        else:
            failed = [c for c in checks if c.get("status") not in {"PASS", "DISABLED"}]
            response: dict[str, object] = {
                "status": "FAIL",
                "checks_count": len(checks),
                "failed_count": len(failed),
                "duration_ms": duration_ms,
                "failed": [
                    {"target": f.get("target", ""), "status": f.get("status", ""), "error": f.get("error")}
                    for f in failed
                ],
            }
            if data.get("staleness"):
                response["staleness"] = data.get("staleness")
            self._send_json(response, status_code=503)

    def _handle_healthz(self):
        ok, details = _readiness_check(STATUS_METRICS_JSON)
        self._send_json(cast("dict[str, object]", cast(object, details)), status_code=200 if ok else 503)

    def _handle_metrics(self):
        """Prometheus text-format metrics (170 W12 C5): deploy SLO + image sizes + backup freshness + TLS.

        ## @purpose  Scrape-endpoint для prometheus (observability-net): platform_deploy_success/
        ##           platform_deploy_duration_seconds (SLO burn-rate), platform_image_size_bytes
        ##           (size-budget), platform_backup_last_postgres_age_seconds (backup-freshness),
        ##           platform_tls_days_left/platform_tls_self_signed (017 C4 — TLS-бандл алерты).
        ## @io       ⎋ text/plain; version=0.0.4 — Prometheus exposition format
        """
        data = get_all_checks()
        metrics_raw = data.get("metrics")
        metrics: MetricsData = metrics_raw if metrics_raw is not None else {}
        node_name = _extract_node_name(_load_node_yaml(NODE_YAML_PATH), fallback=NODE_NAME)

        lines: list[str] = [
            "# HELP platform_deploy_success Last platform deploy result (1=success, 0=failed/partial)",
            "# TYPE platform_deploy_success gauge",
        ]
        deploy = metrics.get("deploy", {})
        deploy_success = 1 if bool(deploy.get("success")) else 0
        lines.append(f'platform_deploy_success{{node="{node_name}"}} {deploy_success}')
        lines.append("# HELP platform_deploy_duration_seconds Duration of the last platform deploy")
        lines.append("# TYPE platform_deploy_duration_seconds gauge")
        duration_s = deploy.get("duration_s")
        lines.append(
            f'platform_deploy_duration_seconds{{node="{node_name}"}} {duration_s if duration_s is not None else "NaN"}'
        )

        lines.append("# HELP platform_image_size_bytes Docker image size per deployed project")
        lines.append("# TYPE platform_image_size_bytes gauge")
        for p in metrics.get("projects", []):
            name = str(p.get("name", "")).replace('"', '\\"')
            image = str(p.get("docker_image", "")).replace('"', '\\"')
            size = p.get("docker_image_size_bytes")
            if size is not None:
                lines.append(f'platform_image_size_bytes{{node="{node_name}",project="{name}",image="{image}"}} {size}')

        lines.append("# HELP platform_backup_last_postgres_age_seconds Age of last PostgreSQL backup (NaN = never)")
        lines.append("# TYPE platform_backup_last_postgres_age_seconds gauge")
        last_pg = metrics.get("backup", {}).get("last_postgres_at")
        age_s: str | float = "NaN"
        ts_s: int = 0  # 0 = never (alert-канал PlatformBackupStale)
        if isinstance(last_pg, str) and last_pg:
            try:
                parsed = datetime.fromisoformat(last_pg.replace("Z", "+00:00"))
                ts_s = int(parsed.timestamp())
                age_s = round(max(time.time() - parsed.timestamp(), 0.0), 2)
            except ValueError:
                age_s = "NaN"
        lines.append(f'platform_backup_last_postgres_age_seconds{{node="{node_name}"}} {age_s}')
        lines.append(
            "# HELP platform_backup_last_postgres_timestamp_seconds Unix ts of last PostgreSQL backup (0 = never)"
        )
        lines.append("# TYPE platform_backup_last_postgres_timestamp_seconds gauge")
        lines.append(f'platform_backup_last_postgres_timestamp_seconds{{node="{node_name}"}} {ts_s}')

        # ── TLS bundle (017 C4): platform_tls_days_left / platform_tls_self_signed ──
        # Только если tls-секция непуста (отсутствие live-директорий → серия не эмитится).
        # Отсутствующие поля → NaN (консистентно стилю deploy_duration).
        tls_section = metrics.get("tls", {})
        if tls_section:
            lines.append("# HELP platform_tls_days_left Days until TLS certificate expiry (NaN = unknown)")
            lines.append("# TYPE platform_tls_days_left gauge")
            lines.append("# HELP platform_tls_self_signed TLS certificate is self-signed (1=yes, 0=no, NaN=unknown)")
            lines.append("# TYPE platform_tls_self_signed gauge")
            for domain_raw, entry in tls_section.items():
                domain = str(domain_raw).replace('"', '\\"')
                days_left = entry.get("days_left")
                days_val: str | float = "NaN" if not isinstance(days_left, int) else str(days_left)
                lines.append(f'platform_tls_days_left{{node="{node_name}",domain="{domain}"}} {days_val}')
                self_signed = entry.get("self_signed")
                self_val: str = "1" if self_signed is True else ("0" if self_signed is False else "NaN")
                lines.append(f'platform_tls_self_signed{{node="{node_name}",domain="{domain}"}} {self_val}')

        self._send("text/plain; version=0.0.4", ("\n".join(lines) + "\n").encode("utf-8"), 200, None)

    def _handle_status_json(self):
        data = get_all_checks()
        metrics_raw = data.get("metrics")
        metrics: MetricsData = metrics_raw if metrics_raw is not None else {}
        node_name = _extract_node_name(_load_node_yaml(NODE_YAML_PATH), fallback=NODE_NAME)
        full_data: dict[str, object] = {
            "status": data.get("status", "FAIL"),
            "generated_at": data.get("generated_at"),
            "duration_ms": data.get("duration_ms", 0),
            "metrics_freshness": data.get("metrics_freshness"),
            "staleness": data.get("staleness"),
            "checks": data.get("checks") or [],
            "schema_version": metrics.get("schema_version", 0),
            "node": node_name,
            "containers": metrics.get("containers", []),
            "certs": metrics.get("certs", []),
            "projects": metrics.get("projects", []),
            "host": metrics.get("host", {}),
            "backup": metrics.get("backup", {}),
            "errors": metrics.get("errors", []),
        }
        self._send_json(full_data, status_code=200 if data.get("status") == "PASS" else 503)

    def _handle_html(self):
        print("[IMP:7][status-page][html] Rendering status page", file=sys.stderr)
        data = get_all_checks()
        freshness = data.get("metrics_freshness") or "unknown"
        self._send_html(_render_html(data), freshness)

    def _handle_refresh(self):
        print("[IMP:7][status-page][refresh] Manual refresh requested", file=sys.stderr)
        self.send_response(302)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # ruff: ignore[A002] — stdlib callback override
        print(f"[IMP:7][status-page][http] {self.client_address[0]} - {format % args}", file=sys.stderr)


# endregion CLASS_StatusPageHandler

# ═══════════════ MAIN ═══════════════
if __name__ == "__main__":
    print(
        f"[IMP:9][status-page][main] Starting status-page on {LISTEN_HOST}:{LISTEN_PORT} "
        f"(node.yaml={NODE_YAML_PATH}, metrics={STATUS_METRICS_JSON})",
        file=sys.stderr,
    )

    server = http.server.ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), StatusPageHandler)
    # W10 T10.11 (M-1): ThreadingHTTPServer — медленный /health НЕ блокирует fast-path /healthz
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[IMP:9][status-page][main] Shutting down", file=sys.stderr)
        server.shutdown()
