"""
# GREP_SUMMARY: test-nginx-ingress-guards, limit_conn, perip-20, slowloris, client-timeouts, keepalive, sse-read-timeout-300, REF-0015, SEC-0045
# STRUCTURE: ▶ glob config/*.conf* → ◇ regex assertions (limit_conn_zone + perip 20 + 4 таймаута в nginx.conf) → ◇ sweep proxy_read_timeout ≤300s → ⎋ PASS | FAIL
# region MODULE_CONTRACT
## @purpose  REF-0015 (DevPlan 11 В2): статические assertions ingress resource guards —
##           production nginx.conf обязан содержать limit_conn_zone/perip 20 и
##           slowloris-таймауты; НИ ОДИН vhost в core/modules/nginx/config/ не держит
##           proxy_read_timeout > 300s (SSE-контракт таймаутов шаблонов).
## @scope    Только файловая система (0 Docker). Синтаксическая валидация директив —
##           tests/gates/test_gate_vhost_nginx_t.py (nginx -t с теми же директивами).
## @invariants
##   - limit_conn на http-уровне наследуется всеми server-блоками (включая project
##     overlays) — наличие в nginx.conf обязательно, дубль в каждом vhost запрещён
##     (TRAP[DECISION] у директивы)
##   - proxy_read_timeout > 300 нигде в config/ — 3600s держал worker-слот 1 час
##     (grafana/langfuse были единственными нарушителями, SEC-0045)
##   - Отсутствие proxy_read_timeout в vhost = nginx default 60s ≤ 300 — compliant
## @rationale Самые дешёвые full-outage векторы платформы: unauthenticated connection-
##           exhaustion (worker_connections 1024) и SSE-слоты без потолка.
# endregion MODULE_CONTRACT
"""

import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

pytestmark = pytest.mark.static_audit

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "core" / "modules" / "nginx" / "config"
NGINX_CONF = CONFIG_DIR / "nginx.conf"


def _conf_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015/SEC-0045 — директивы resource guards в nginx.conf
# · Scenario: limit_conn_zone + limit_conn perip 20 + client_header/body_timeout 10s +
# ·   send_timeout 30s + keepalive_timeout ≤15s присутствуют на http-уровне.
# · Last fail: prior — client-timeout grep=0, только limit_req_zone (evidence REF-0015).
# · Remove if: guards переезжают на другой механизм (WAF/CDN — вне окна)
@ldd_trajectory
def test_nginx_conf_has_resource_guard_directives() -> None:
    """nginx.conf содержит limit_conn (perip 20) и все четыре client-таймаута."""
    text = _conf_text(NGINX_CONF)

    assert re.search(r"limit_conn_zone\s+\$binary_remote_addr\s+zone=perip:\d+m\s*;", text), (
        "limit_conn_zone $binary_remote_addr zone=perip обязателен (REF-0015):\n" + text
    )
    assert re.search(r"(?m)^\s*limit_conn\s+perip\s+20\s*;", text), (
        "limit_conn perip 20 на http-уровне обязателен — наследуется всеми vhosts:\n" + text
    )
    for directive in ("client_header_timeout", "client_body_timeout", "send_timeout"):
        assert re.search(rf"(?m)^\s*{directive}\s+\d+s\s*;", text), f"{directive} отсутствует (REF-0015)"
    keepalive = re.search(r"(?m)^\s*keepalive_timeout\s+(\d+)s?\s*;", text)
    assert keepalive, "keepalive_timeout отсутствует"
    assert int(keepalive.group(1)) <= 15, (
        f"keepalive_timeout {keepalive.group(1)}s > 15s — slowloris-слоты держатся слишком долго"
    )
    logger.info("[IMP:9][test][nginx-guards] nginx.conf: limit_conn perip 20 + 4 таймаута OK")


# 🧪 TRAP[TEST] · 2026-08-25 · unit · REF-0015 — SSE read_timeout ≤300s во ВСЕХ vhosts config/
# · Scenario: sweep всех *.conf/*.conf.template — ни один proxy_read_timeout > 300;
# ·   отсутствие директивы = nginx default 60s — compliant.
# · Last fail: prior — grafana/langfuse держали proxy_read_timeout 3600s (SSE до 1h).
# · Remove if: SSE-потолок пересматривается (карточка REF-0015: «≤300s»)
def test_all_vhosts_sse_read_timeout_within_cap() -> None:
    """proxy_read_timeout ≤ 300s в каждом конфиге config/ (SSE template-contract)."""
    conf_files = sorted(CONFIG_DIR.glob("*.conf*"))
    assert len(conf_files) > 0, f"нет vhost-конфигов в {CONFIG_DIR}"

    offenders: list[str] = []
    checked = 0
    for path in conf_files:
        for value in re.findall(r"(?m)^\s*proxy_read_timeout\s+(\d+)s?\s*;", _conf_text(path)):
            checked += 1
            if int(value) > 300:
                offenders.append(f"{path.name}: proxy_read_timeout {value}s")

    assert checked >= 2, "ожидаются явные proxy_read_timeout хотя бы в двух vhosts (sweep живой)"
    assert not offenders, f"SSE read_timeout >300s запрещён (REF-0015): {offenders}"
    logger.info(
        "[IMP:9][test][nginx-guards] SSE cap: %d proxy_read_timeout значений проверены — все ≤300s",
        checked,
    )
