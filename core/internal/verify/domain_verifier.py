#!/usr/bin/env python3
"""
# GREP_SUMMARY: domain_verifier, node-yaml, expose-domains, curl-verify, status-page, health-check
# STRUCTURE: ▶ ┌node_name,platform_root┐ → ○ resolve_node_yaml (3-path) → ○ get_expose_domains ┌projects[expose=true]┐
#            → ○ _verify_domains foreach: ┌curl --max-time (общий shared/http_probe.curl_http_code)┐ → ◇ HTTP 200? → ⊕ pass|warn|connection_error
#            → ○ _check_status_page(env) (Basic Auth) → ∑ results → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Post-deploy HTTP(S) domain verification — resolve node.yaml via 3-path search, parse
##            expose:true domains, curl each domain for HTTP 200, check status-page /health endpoint
## @scope    Business logic extracted from verify-domains.sh via Strangler-Fig (Wave 5a).
##           Called directly from core/entrypoints/verify.sh (make verify-domains) and
##           context_deployer.py::_step_verify — двух-хоповый фасад verify-domains.sh удалён
##           (DevPlan 173 W1.5); or directly from Python tests.
## @invariants
##   - resolve_node_yaml: searches 3 paths (platform-local → org repos → VPS fallback)
##   - get_expose_domains: only extracts projects with `expose: true` (strict boolean);
##     `project` аргумент сужает скоуп до одного проекта (verify per-project, P-22)
##   - verify_domain: uses subprocess.run(["curl", ...]) — preserves same TLS stack as shell
##   - verify_status_page: Basic Auth via curl -u, URL = platform.{domain}/health
##   - Exit code 0: ALL domains + status-page respond HTTP 200
##   - Exit code 1: at least one domain unreachable, non-200, or status-page fails
##   - HTTPS is always used (https://${domain})
## @rationale Strangler-Fig migration per DevPlan 036A D1. Full Python port eliminates 2 inline
##            python3 -c blocks and enables unit-testing of all domain verification logic.
##            curl kept as subprocess (not requests) for TLS stack parity (DevPlan 036A D3).
##            DevPlan 125 T1 (P-22): --project — CI-verify деплоящегося проекта не зависит
##            от 502 соседа при параллельном деплое (verify-race закрыт системно).
## @changes  2026-07-26 | Wave 5a — Created via Strangler-Fig from verify-domains.sh (281→59 LOC)
##           2026-08-03 | DevPlan 125 T1 — +--project (verify per-project, P-22)
##           2026-08-15 | План 170 W7-E1 — main 120/CC15 → _verify_domains/_check_status_page(env)/
##                      _render_domain_lines; дедуп curl _verify_domain vs _verify_status_page
##                      (~50 LOC) → общий shared/http_probe.curl_http_code (172 W5.4); env через параметр (DI-гигиена)
##           2026-08-16 | DevPlan 173 W1.5 — verify-domains.sh удалён; entrypoint verify.sh →
##                      domain_verifier.py напрямую (positional node/project + env fallback)
# endregion MODULE_CONTRACT
"""

import argparse
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

# B3: канонический platform base — shared/deploy_paths (литерал /opt/platform удалён)
from core.internal.shared.deploy_paths import platform_remote_base

# REF-0107: Config*Error — ТОЛЬКО канонический импорт-путь shared.exceptions (детектор
# exception-import-path; re-export node_yaml создаёт второй путь к тем же классам).
from core.internal.shared.exceptions import ConfigNotFoundError, ConfigParseError

# W1-A1 (план 170): STATUS_PAGE_TIMEOUT=30 (дубль SoT) → SSH_CONNECT_TIMEOUT (30) — каноническое
# 30s окно HTTP/ssh-проверок; значение идентично, источник значений — единый реестр timeouts.py.
from core.internal.shared.http_probe import curl_http_code
from core.internal.shared.node_yaml import NodeYaml
from core.internal.shared.timeouts import SSH_CONNECT_TIMEOUT

logger = logging.getLogger(__name__)

# ── Module-level constants ────────────────────────────────────────────────
CURL_TIMEOUT_DEFAULT = 10
HTTP_OK: int = 200  # успешный HTTP-статус (verify pass)
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

    # ruff: ignore[PLR6301]  # интерфейс-колбек: override logging.Filter.filter (instance-метод базового класса)
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


# region CURL_HELPER


# endregion CURL_HELPER


# region FUNC_RESOLVE_NODE_YAML


def resolve_node_yaml(node_name: str, platform_root: Path) -> Path:
    """Resolve node.yaml via NodeYaml.resolve() — single source of truth.

    ## @purpose  3-path resolution delegated to NodeYaml.resolve()
    ## @param node_name     Node name
    ## @param platform_root Base path for platform-local lookup
    ## @returns Absolute Path to existing node.yaml
    ## @raises FileNotFoundError If none of the 3 paths contain the file
    ## @complexity O(1) — at most 3 path probes via NodeYaml.resolve()
    ## @invariants
    ##   1. Delegates to NodeYaml.resolve() — single source of truth for 3-path resolution
    ##   2. Converts ConfigNotFoundError → FileNotFoundError for backward compat
    ##   3. Returns absolute Path to found node.yaml
    ## @rationale Replaces 3 hand-rolled path probes with unified NodeYaml.resolve().
    ##            DevPlan 088 Wave 2 T2.5: eliminate redundancy across 3 resolve_node_yaml implementations.
    """
    try:
        ny = NodeYaml.resolve(node_name=node_name, config_dir=str(platform_root))
        result = Path(ny._path)
        logger.info("[IMP:9][resolve_node_yaml][found] Resolved via NodeYaml: %s", result)
    except ConfigNotFoundError as e:
        logger.error("[IMP:10][resolve_node_yaml][error] %s", e)
        raise FileNotFoundError(str(e)) from e
    else:
        return result


# endregion FUNC_RESOLVE_NODE_YAML


# region FUNC_GET_EXPOSE_DOMAINS


def get_expose_domains(yaml_path: Path, project: str | None = None) -> list[str]:
    """Parse node.yaml and extract domains from projects with expose:true.

    ## @purpose  Read YAML file, filter projects where expose==True (strict boolean),
    ##            collect their domain values. Optional `project` restricts the scope
    ##            to a single project's expose domain (verify per-project, P-22).
    ## @param yaml_path Path to node.yaml file
    ## @param project   Project name (registry-имя из node.yaml projects[].name) —
    ##                  None = прежнее поведение (все expose:true домены ноды)
    ## @returns List of domain strings (may be empty)
    ## @complexity O(n) where n = number of projects in YAML
    ## @invariants
    ##   - Only boolean `True` matches — string "true" does NOT match
    ##   - Projects without a `domain` key are silently skipped
    ##   - Returns empty list if YAML is empty or has no projects key
    ##   - YAML parse errors propagate as exceptions
    ##   - project != None → фильтр по projects[].name (параллельный деплой соседнего
    ##     проекта не даёт ложный FAIL: 502 соседа в момент verify вне скоупа)
    ## @rationale DevPlan 125 T1 (P-22/D-14): domain_verifier брал ВСЕ expose-домены
    ##            ноды → параллельный деплой соседнего проекта = 502 → ложный FAIL CI.
    ##            CI-verify теперь сужает скоуп до деплоящегося проекта; `make verify-domains`
    ##            без PROJECT сохраняет прежнее поведение (обратная совместимость).
    """
    logger.info("[IMP:8][get_expose_domains][parse] Reading: %s (project=%s)", yaml_path, project)
    node = NodeYaml(str(yaml_path))
    projects = node.get_projects()
    domains: list[str] = []
    for p in projects:
        if p.get("expose", False) is True:
            if project is not None and p.get("name") != project:
                logger.info(
                    "[IMP:8][get_expose_domains][filter] Project %s вне скоупа verify (scope=%s) — skip",
                    p.get("name"),
                    project,
                )
                continue
            domain = p.get("domain")
            if domain:
                domains.append(str(domain))

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
    ##   - Flags: -sS -o /dev/null -w '%{http_code}' --max-time {t} (сборка — _curl_http_code)
    ##   - HTTPS only: https://{domain}
    ##   - Сигнатура сохранена 1:1 (тесты mock'ают subprocess.run — рантайм-резолв в helper)
    ## @rationale D3: curl subprocess keeps same TLS stack (OpenSSL) as original verify-domains.sh.
    ##            W7-E1: curl-обвязка дедуплицирована с verify_status_page → _curl_http_code.
    """
    code, err = curl_http_code(f"https://{domain}", timeout, timeout_label="Connection")
    if err is not None:
        return VerifyResult(domain=domain, status="connection_error", http_code=None, error=err)

    if code == HTTP_OK:
        logger.info("[IMP:9][verify_domain][pass] %s — HTTP 200 ✓", domain)
        return VerifyResult(domain=domain, status="pass", http_code=code)
    logger.info("[IMP:9][verify_domain][warn] %s — HTTP %d ⚠️ (expected 200)", domain, code)
    return VerifyResult(domain=domain, status="warn", http_code=code)


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
    ##   - Таймаут — SSH_CONNECT_TIMEOUT (30, канон timeouts.py, W1-A1); curl-обвязка — _curl_http_code
    ##   - Сигнатура сохранена 1:1 (тесты mock'ают subprocess.run — рантайм-резолв в helper)

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
    code, err = curl_http_code(
        status_page_url,
        SSH_CONNECT_TIMEOUT,
        timeout_label="Status-page",
        extra_args=["-u", f"{email}:{password}"],
    )
    if err is not None:
        return VerifyResult(domain=status_page_url, status="connection_error", http_code=None, error=err)

    if code == HTTP_OK:
        logger.info("[IMP:9][verify_status_page][pass] Status-page /health — HTTP 200 PASS ✓")
        return VerifyResult(domain=status_page_url, status="pass", http_code=code)
    logger.error("[IMP:9][verify_status_page][fail] Status-page /health — HTTP %d FAIL ⚠️", code)
    return VerifyResult(domain=status_page_url, status="warn", http_code=code)


# endregion FUNC_VERIFY_STATUS_PAGE


# region FUNC__RENDER_DOMAIN_LINES


def _render_domain_lines(results: list[VerifyResult]) -> list[str]:
    """Строки результатов verify доменов (формат shell-версии, W7-E1).

    ▶ ┌results┐ → ○ per-result: pass → '✓' | connection_error → error | warn → '⚠️ WARN' → ⎋ list[str]

    ## @purpose — Рендер строк доменов отдельно от оркестрации (main 120/CC15 → декомпозиция):
    ##            чистый форматтер без сайд-эффектов, печать — ответственность вызывающего.
    ## @io — ⇥ results: list[VerifyResult] → ⎋ list[str] (строки печати, 1 на домен)
    ## @complexity — O(N) где N = домены
    ## @invariants — Формат строк 1:1 с прежним main: '  {domain} -> HTTP {code} ✓' /
    ##               '  {domain} -> {error}' / '  {domain} -> HTTP {code} ⚠️  WARN: expected 200'
    """
    lines: list[str] = []
    for r in results:
        if r.status == "pass":
            lines.append(f"  {r.domain} -> HTTP {r.http_code} ✓")
        elif r.status == "connection_error":
            lines.append(f"  {r.domain} -> {r.error}")
        else:
            lines.append(f"  {r.domain} -> HTTP {r.http_code} ⚠️  WARN: expected 200")
    return lines


# endregion FUNC__RENDER_DOMAIN_LINES


# region FUNC__VERIFY_DOMAINS


def _verify_domains(domains: list[str], timeout: int, project: str | None = None) -> bool:
    """Оркестрация verify всех expose:true доменов (W7-E1, из main 120/CC15).

    ▶ ┌domains, timeout┐ → ◇ пусто? → PASS (0 доменов) | ○ per-domain verify_domain → ⊕ печать строк
      → ∑ all_ok → ⎋ bool

    ## @purpose — Цикл verify доменов + печать строк (stdout, формат shell-версии).
    ##            Возвращает all_ok: False при ≥1 connection_error/warn (exit 1).
    ## @io — ⇥ domains: list[str]; timeout: int (curl --max-time); project: str | None
    ##       (scope-нота лога «No expose:true domains found for project …») → ⎋ bool (all_ok)
    ## @complexity — O(N) где N = домены (каждый — 1 subprocess)
    ## @invariants
    ##   - 0 доменов → True (паритет «no expose:true domains → PASS», DevPlan 136 AC W5)
    ##   - all_ok = все results.status == 'pass' (warn/connection_error → False)
    ##   - Печать строк через _render_domain_lines (формат 1:1 с прежним main)
    ##   - Список доменов логируется [IMP:8] (траектория видна QA)
    """
    if not domains:
        scope_note = f" for project {project!r}" if project else ""
        logger.info("[IMP:9][_verify_domains][ok] No expose:true domains found%s — nothing to verify", scope_note)
        print("")
        logger.info("[IMP:9][_verify_domains][ok] ALL DOMAINS PASS — 0 domain(s) to check")
        return True

    logger.info("[IMP:7][_verify_domains][domains] Found %d expose:true domain(s) to check", len(domains))
    for d in domains:
        logger.info("[IMP:8][_verify_domains][domain]   - %s", d)

    results = [verify_domain(d, timeout=timeout) for d in domains]
    for line in _render_domain_lines(results):
        print(line)

    all_ok = all(r.status == "pass" for r in results)
    print("")
    if all_ok:
        logger.info("[IMP:9][_verify_domains][ok] ALL DOMAINS PASS — HTTP 200 for all %d domain(s)", len(domains))
    else:
        logger.info("[IMP:9][_verify_domains][fail] SOME DOMAINS FAILED — review output above")
    return all_ok


# endregion FUNC__VERIFY_DOMAINS


# region FUNC__CHECK_STATUS_PAGE


def _check_status_page(env: Mapping[str, str]) -> VerifyResult | None:
    """Status-page /health проверка с env через параметр (DI-гигиена, W7-E1).

    ▶ ┌env┐ → ◇ PLATFORM_DOMAIN/MASTER_EMAIL/MASTER_PASSWORD → verify_status_page → ⎋ VerifyResult | None

    ## @purpose — Чтение env-переменных status-page вынесено из main в отдельную функцию с
    ##            явным параметром env (Mapping): тесты могут передать dict без monkeypatch
    ##            setenv (план 170 W7-E1 «тесты не должны прибавлять setenv»); CLI передаёт os.environ.
    ## @io — ⇥ env: Mapping[str, str] → ⎋ VerifyResult | None (None = credentials отсутствуют, skip)
    ## @complexity — O(1) + 1 subprocess
    ## @invariants
    ##   - Ключи: PLATFORM_DOMAIN, PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD
    ##   - Отсутствие домена или credentials → None (skip, НЕ ошибка — паритет прежнего main)
    ##   - Поведение 1:1: main(CLI) передаёт os.environ — пустой env = skip status-page
    """
    platform_domain = env.get("PLATFORM_DOMAIN", "")
    master_email = env.get("PLATFORM_MASTER_EMAIL", "")
    master_password = env.get("PLATFORM_MASTER_PASSWORD", "")

    sp_result = verify_status_page(platform_domain, master_email, master_password)
    if sp_result is None:
        logger.info(
            "[IMP:8][_check_status_page][skip] Skipping status-page health check — missing PLATFORM_DOMAIN or credentials"
        )
    return sp_result


# endregion FUNC__CHECK_STATUS_PAGE


# region FUNC_MAIN


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for domain verification.

    ## @purpose  Parse command-line arguments, orchestrate full verification pipeline:
    ##            resolve node.yaml → parse expose:true domains → curl each → status-page check.
    ## @param argv  Argument list (defaults to sys.argv[1:])
    ## @returns Exit code 0 (all pass) or 1 (any fail)
    ## @complexity O(n) where n = number of domains + 1 status-page check
    ## @invariants
    ##   - Requires node name (positional <node> / --node <node> / NODE env) — DevPlan 173 W1.5
    ##   - Reads PLATFORM_DOMAIN, PLATFORM_MASTER_EMAIL, PLATFORM_MASTER_PASSWORD from env for status-page
    ##     (через _check_status_page(os.environ) — DI-гигиена W7-E1)
    ##   - Prints per-domain status lines to stdout (same format as shell version)
    ##   - Exit 0: ALL domains + status-page pass
    ##   - Exit 1: any domain or status-page fails (или отсутствует node_name)
    ## @changes 2026-08-15 | План 170 W7-E1 — 120 LOC/CC15 → _verify_domains/_check_status_page/
    ##           _render_domain_lines (оркестрация шагов ниже — тонкие делегаты)
    ## @changes 2026-08-16 | DevPlan 173 W1.5 — +positional node/project (entrypoint verify.sh → exec
    ##           domain_verifier.py напрямую, двух-хоповый verify-domains.sh удалён); env NODE/PROJECT fallback
    """
    _setup_logging()

    parser = argparse.ArgumentParser(description="Domain verification tool (Wave 5a)")
    parser.add_argument("command", choices=["verify"], help="Subcommand (only 'verify' supported)")
    parser.add_argument("node", nargs="?", default=None, help="Node name (positional)")
    parser.add_argument("project", nargs="?", default=None, help="Project name (positional)")
    parser.add_argument("--node", dest="node_opt", default=None, help="Node name (flag)")
    parser.add_argument(
        "--project",
        dest="project_opt",
        default=None,
        help="Project name — restrict verification to this project's expose domain "
        "(verify per-project, DevPlan 125 T1; без --project — все expose:true домены ноды)",
    )
    parser.add_argument("--platform-root", default=str(platform_remote_base()), help="Platform root path")
    parser.add_argument("--curl-timeout", type=int, default=CURL_TIMEOUT_DEFAULT, help="Curl timeout in seconds")

    # argparse.Namespace → типизированная граница (W11): двойной cast через object
    @dataclass
    class _CliArgs:
        command: str
        node: str
        project: str
        node_opt: str
        project_opt: str
        platform_root: str
        curl_timeout: int

    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    # 173 W1.5: резолв node/project — flag → positional → env (перенесено из verify.sh);
    # пустая строка → None (паритет `${project:+--project}` из verify-domains.sh)
    node_name = args.node_opt or args.node or os.environ.get("NODE", "")
    project = (args.project_opt or args.project or os.environ.get("PROJECT")) or None

    if not node_name:
        logger.error("[IMP:10][main][error] Missing required argument: node_name")
        parser.print_usage(sys.stderr)
        return 1

    platform_root = Path(args.platform_root)
    curl_timeout = args.curl_timeout

    logger.info(
        "[IMP:7][main][start] Starting post-deploy verification for node=%s project=%s",
        node_name,
        project or "(all)",
    )

    # Step 1: Resolve node.yaml
    try:
        yaml_path = resolve_node_yaml(node_name, platform_root)
    except FileNotFoundError as e:
        logger.error("[IMP:10][main][error] %s", e)
        return 1
    logger.info("[IMP:7][main][resolve] Resolved node.yaml: %s", yaml_path)

    # Step 2: Parse expose:true domains
    logger.info("[IMP:7][main][parse] Parsing projects with expose:true from %s", yaml_path)
    try:
        domains = get_expose_domains(yaml_path, project=project)
    except (ConfigNotFoundError, ConfigParseError, OSError) as e:
        logger.error("[IMP:10][main][parse] Failed to parse YAML: %s — %s", yaml_path, e)
        return 1

    # Step 3: Verify each domain
    all_ok = _verify_domains(domains, curl_timeout, project=project)

    # Step 4: Status-page health check (env через параметр — DI-гигиена)
    sp_result = _check_status_page(os.environ)
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
