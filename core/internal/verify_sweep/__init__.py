# GREP_SUMMARY: verify-sweep, e2e-verify, sweep, cli, re-export, python-module, exit-contract, devplan-136
# STRUCTURE: ┌re-export (models/collection/http_check/tls_check/report)┐ → ⊕ CLI (_build_parser + main: collect → checks → render) → ⎋ if __name__ == "__main__"
# region MODULE_CONTRACT
## @purpose  Пакет verify_sweep (DevPlan 136 W5 T5.1-T5.6): endpoint sweep-верификация для
##           `make e2e-verify`. __init__.py — фасад: re-export всех публичных символов
##           монолита (обратная совместимость импортов) + CLI (python3 -m core.internal.verify_sweep sweep).
## @scope    CLI-слой (argparse, main-оркестрация, __main__) + re-export-контракт пакета.
##           Бизнес-логика: models.py (типы), collection.py (сбор), http_check.py / tls_check.py
##           (проверки), report.py (текстовый отчёт).
## @invariants
##   - CLI-имя сохранено: `python3 -m core.internal.verify_sweep sweep` (ci.mk:63, entrypoint-manifest.yaml:215)
##   - Re-export 1:1 всех символов монолита: тест-импорты (tests/unit/test_verify_sweep.py:28-29)
##     и приватный _default_remote_nginx_conf_dir (:628) работают без изменений
##   - main() НЕ вызывает sys.exit — возвращает int (канон core/AGENTS.md); sys.exit только
##     в __main__ guard
##   - Exit-контракт 0/1/2: 0 = все endpoints OK; 1 = ≥1 FAIL (HTTP/TLS/collection, R4);
##     2 = config error (node.yaml не найден, node.host не резолвится, неизвестный mode)
##   - --json → отчёт в stdout (json.dumps), текстовый отчёт и логи — в stderr
##   - 0 endpoints → exit 0 (голая test-e2e нода, I6)
## @rationale Декомпозиция монолита verify_sweep.py (1284 LOC) → пакет (план 170 W7-E1,
##            research-A §7): файл → каталог с моделями/коллекцией/проверками/отчётом.
##            CLI-имя и exit-контракт — инварианты make/CI (ci.mk, manifest) — сохранены 1:1.
##            __main__-совместимость через `if __name__ == "__main__"` в __init__.py
##            (канон W3: python -m работает и для пакета с __init__.py).
## @changes  2026-08-15 | План 170 W7-E1 — монолит verify_sweep.py (1284 LOC) → пакет (чистый move)
## @usecases
##   - Оператор/агент: `make e2e-verify NODE=tronyx-vps` — таблица endpoint→HTTP→TLS→вердикт
##   - QA/диагностика: `make e2e-verify NODE=<n> JSON=1` — machine-readable отчёт
# endregion MODULE_CONTRACT

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from typing import cast

from core.internal.verify_sweep.collection import (
    DEFAULT_SSH_USER,
    REMOTE_NGINX_CONF_DIR,
    NodeContext,
    collect_endpoints,
    parse_nginx_server_names,
)
from core.internal.verify_sweep.collection import (
    default_remote_nginx_conf_dir as _default_remote_nginx_conf_dir,
)
from core.internal.verify_sweep.http_check import (
    CURL_TIMEOUT_DEFAULT,
    check_http,
    classify_http_code,
)
from core.internal.verify_sweep.models import (
    SSL_PORT,
    Endpoint,
    EndpointCollectionError,
    HttpResult,
    SweepReport,
    TlsResult,
)
from core.internal.verify_sweep.report import render_text_report as _render_text_report
from core.internal.verify_sweep.tls_check import (
    EXPIRY_WARN_DAYS,
    OPENSSL_TIMEOUT_DEFAULT,
    check_tls,
    expiry_verdict,
    san_matches_domain,
)

logger = logging.getLogger(__name__)

# Приватные re-export контракты (private-imports гейт U-07 разрешает `from X import name as _alias`:
# публичная сущность в подмодуле, приватный только алиас — канон check_suite W3, research-A §7):
#   - _default_remote_nginx_conf_dir — тест-контракт tests/unit/test_verify_sweep.py:628
#   - _render_text_report — используется main() (не-json отчёт в stderr)
# Эти имена НЕ входят в __all__ (не публичный API), но доступны как атрибуты пакета.

__all__ = [
    "CURL_TIMEOUT_DEFAULT",
    "DEFAULT_SSH_USER",
    "EXPIRY_WARN_DAYS",
    "OPENSSL_TIMEOUT_DEFAULT",
    "REMOTE_NGINX_CONF_DIR",
    "SSL_PORT",
    "Endpoint",
    "EndpointCollectionError",
    "HttpResult",
    "NodeContext",
    "SweepReport",
    "TlsResult",
    "check_http",
    "check_tls",
    "classify_http_code",
    "collect_endpoints",
    "expiry_verdict",
    "main",
    "parse_nginx_server_names",
    "san_matches_domain",
]


# region CLI


# region FUNC__build_parser
def _build_parser() -> argparse.ArgumentParser:
    """CLI-парсер verify_sweep (sweep subcommand).

    ▶ ┌None┐ → ⊕ argparse.ArgumentParser → ⎋ parser
    ## @purpose — CLI: `python3 -m core.internal.verify_sweep sweep --node N [--mode ...] [--json] ...`
    ## @io — ⇥ None → ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(description="Endpoint sweep verification (DevPlan 136 W5 e2e-verify)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sweep = sub.add_parser("sweep", help="Sweep-verify all endpoints of a node")
    p_sweep.add_argument("--node", required=True, help="Node name (resolved via node.yaml / NODE_HOST_MAP)")
    p_sweep.add_argument(
        "--mode",
        choices=("local", "remote"),
        default="remote",
        help="Endpoint collection source: local (node.yaml + overlays/nginx) | remote (ssh nginx conf.d, default)",
    )
    p_sweep.add_argument("--json", action="store_true", help="Print machine-readable JSON report to stdout")
    p_sweep.add_argument("--node-configs-dir", default=None, help="Path to node-configs/ (local mode overlay scan)")
    p_sweep.add_argument("--platform-root", default=None, help="Platform root for node.yaml 3-path search")
    p_sweep.add_argument("--timeout", type=int, default=CURL_TIMEOUT_DEFAULT, help="HTTP/TLS timeout in seconds")
    p_sweep.add_argument(
        "--ssh-user", default=DEFAULT_SSH_USER, help="SSH user for remote collect (default: ci-deploy)"
    )
    p_sweep.add_argument(
        "--nginx-conf-dir",
        default=None,
        help="Remote nginx conf.d dir for remote collect (default: /opt/node-configs/<node>/overlays/nginx, DevPlan 153 T5)",
    )
    return parser


# endregion FUNC__build_parser


# region FUNC_main
def main(argv: list[str] | None = None) -> int:
    """CLI entry: sweep endpoint collection + HTTP/TLS checks → отчёт → exit 0/1/2.

    ▶ ┌argv┐ → ◇ parse → ◇ collect_endpoints (EndpointCollectionError → exit 1|2)
      → ○ check_http/check_tls per endpoint ([IMP:9] на каждый вердикт)
      → ⊕ render (json|text) → ⎋ exit 0|1|2

    ## @purpose — Оркестрация полного sweep (DevPlan 136 T5.1): collection → checks →
    ##            отчёт. Каждый вердикт логируется [IMP:9] (LDD телеметрия для QA);
    ##            --json печатает machine-readable отчёт в stdout, логи — в stderr.
    ## @io — ⇥ argv: list[str] | None → ⎋ int (0 all ok | 1 ≥1 FAIL | 2 config error)
    ## @complexity — O(E * (H + T)) где E = endpoints, H = HTTP check, T = TLS check
    ## @invariants
    ##   - main() НЕ вызывает sys.exit — возвращает int (канон core/AGENTS.md)
    ##   - EndpointCollectionError exit_code=2 (config) / 1 (operational FAIL) пробрасывается как есть
    ##   - [IMP:9] на каждый HTTP и TLS вердикт (анти-иллюзия: траектория видна QA)
    ##   - --json → отчёт в stdout (json.dumps), текстовый отчёт в stderr
    ##   - 0 endpoints → exit 0 (голая test-e2e нода, I6)
    ##   - Логи в stderr через logging (stdout зарезервирован под отчёт/JSON)
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    parser = _build_parser()

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    @dataclass
    class _CliArgs:
        command: str
        node: str
        mode: str
        json: bool
        node_configs_dir: str
        platform_root: str
        timeout: int
        ssh_user: str
        nginx_conf_dir: str

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))
    # 170 W10-C (D3c): subparsers required=True с единственной командой "sweep" →
    # args.command всегда "sweep"; ветка "!= sweep" недостижима — удалена (parser.error на
    # неизвестную команду делает сам argparse: unrecognized arguments → SystemExit 2).

    logger.info("[IMP:7][main] e2e-verify sweep start: node=%s mode=%s json=%s", args.node, args.mode, args.json)

    # ── Collection ───────────────────────────────────────────────────
    try:
        endpoints = collect_endpoints(
            args.node,
            mode=args.mode,
            node_configs_dir=args.node_configs_dir,
            platform_root=args.platform_root,
            remote_conf_dir=args.nginx_conf_dir,
            ssh_user=args.ssh_user,
        )
    except EndpointCollectionError as exc:
        logger.error("[IMP:10][main] Endpoint collection failed (exit=%d): %s", exc.exit_code, exc)
        print(f"e2e-verify FAIL — endpoint collection error: {exc}", file=sys.stderr)
        return exc.exit_code

    report = SweepReport(node=args.node, mode=args.mode, endpoints=len(endpoints))

    # ── Checks (per endpoint: HTTP + TLS, [IMP:9] verdict) ───────────
    for ep in endpoints:
        http_result = check_http(ep, timeout=args.timeout)
        tls_result = check_tls(ep, timeout=args.timeout)
        report.http.append(http_result)
        report.tls.append(tls_result)
        logger.info(
            "[IMP:9][main] verdict %s: http=%s tls=%s (exit-so-far=%d)",
            ep.fqdn,
            http_result.verdict,
            tls_result.verdict,
            0 if (http_result.ok and tls_result.ok) else 1,
        )

    # ── Report ───────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render_text_report(report), file=sys.stderr)

    if report.exit_code == 0:
        logger.info("[IMP:9][main] e2e-verify PASS — %d endpoint(s) all green", report.endpoints)
    else:
        logger.info("[IMP:9][main] e2e-verify FAIL — %d endpoint(s), review table", report.endpoints)
    return report.exit_code


# endregion FUNC_main

# endregion CLI


if __name__ == "__main__":
    sys.exit(main())
