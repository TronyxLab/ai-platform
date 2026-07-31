#!/usr/bin/env python3
# GREP_SUMMARY: conflict-checks fqdn ports validate ai-platform-yaml uniqueness deploy-block grep-removal strangler
# STRUCTURE: ▶ check_fqdn_conflict(project_dir, projects_base) → ○ scan ai-platform.yaml → ○ compare domains → ⎋ (ok, msg)
#            ▶ check_port_conflict(projects_base) → ○ scan monitoring.host_port → ○ port_map → ⎋ (ok, msg)
#            ▶ CLI: --check-fqdn <dir> | --check-ports [base] → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  FQDN и host_port uniqueness checks между проектами ноды (06 §5.4 E1, E2).
##           Python-порт grep-based shell-логики validate.sh (check_fqdn_conflict/check_port_conflict,
##           Strangler Tier-2, debt cleanup 2026-07-31). Заменяет fragile grep '^\s*domain:' + awk.
## @scope    Вызывается validate.sh --check-fqdn / --check-ports (фасад) и напрямую
##           DeployEngine._preflight_checks (subprocess). Импортируемый: check_fqdn_conflict().
## @invariants
##   - Первый проект, заявивший FQDN, владеет им; второй — E1 conflict (deploy blocked)
##   - Не-доменные значения (false/none/no/null/пусто) пропускаются (TRAP[BUG] false-positive)
##   - Только ai-platform.yaml (legacy declaration files удалены per AD-2)
##   - check_port_conflict: только monitoring.host_port; проекты без порта пропускаются
##   - Exit-код CLI: 0 = ok, 1 = conflict/error
## @rationale grep-based YAML parsing в shell — источник false-позитивов (TRAP[BUG] needs.domain: false);
##            Python yaml.safe_load устраняет класс ошибок. Языковая политика: бизнес-логика в Python.
## @changes 2026-07-31 | Создан (validate.sh Strangler, debt S-1)
# endregion MODULE_CONTRACT

import argparse
import logging
import sys
from pathlib import Path

import yaml

logger = logging.getLogger("conflict_checks")

_FALSEY_DOMAINS = {"false", "none", "no", "null", ""}


def _extract_domain(project_yaml: Path) -> str:
    """Extract domain from ai-platform.yaml (needs.domain → top-level domain fallback).

    ▶ ┌yaml_path┐ → ○ safe_load → ○ needs.domain | domain → ⎋ str (normalized, "" if none)
    """
    try:
        with open(project_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as e:
        logger.warning("[IMP:7][fqdn] Cannot parse %s: %s", project_yaml, e)
        return ""
    if not isinstance(data, dict):
        return ""
    needs = data.get("needs", {})
    domain = needs.get("domain", "") if isinstance(needs, dict) else ""
    if not domain:
        domain = data.get("domain", "")
    return str(domain).strip().lower()


def check_fqdn_conflict(project_dir: str, projects_base: str | None = None) -> tuple[bool, str]:
    """Check FQDN uniqueness of project_dir against all projects on the node.

    ▶ ┌project_dir, projects_base┐ → ○ own domain → ○ scan base → ○ compare → ⎋ (ok, message)

    ## @purpose — E1: FQDN уникален на ноде; конфликт блокирует deploy.
    ## @io — ⇥ project_dir: str — проверяемый проект
    ##       ⇥ projects_base: str | None — корень проектов (default: PROJECTS_BASE env или <root>/projects)
    ##       → ⎋ (bool, str) — (True, "ok-msg") | (False, "E1 conflict msg")
    ## @complexity — O(P) где P = число ai-platform.yaml в projects_base
    ## @invariants — non-domain values (false/none/no/null) пропускаются; own project не сравнивается
    """
    project_dir_path = Path(project_dir)
    project_yaml = project_dir_path / "ai-platform.yaml"
    if not project_yaml.is_file():
        return True, f"No ai-platform.yaml in {project_dir} — skipping FQDN check"

    own_domain = _extract_domain(project_yaml)
    if own_domain in _FALSEY_DOMAINS:
        return True, f"No domain declared in {project_dir} — skipping FQDN check"

    logger.info("[IMP:7][fqdn] Checking FQDN uniqueness: '%s' claimed by %s", own_domain, project_dir_path.name)

    if not projects_base:
        projects_base = _resolve_projects_base()
    base = Path(projects_base)
    if not base.is_dir():
        return True, f"PROJECTS_BASE ({base}) not available — skipping cross-project FQDN check"

    for other_yaml in sorted(base.glob("*/ai-platform.yaml")):
        if other_yaml.parent == project_dir_path:
            continue
        other_domain = _extract_domain(other_yaml)
        if other_domain and other_domain == own_domain:
            msg = f"E1: FQDN '{own_domain}' already claimed by '{other_yaml.parent.name}' — deploy blocked (06 §5.4)"
            logger.error("[IMP:10][fqdn] %s", msg)
            return False, msg

    logger.info("[IMP:9][fqdn] FQDN '%s' is unique across projects on this node", own_domain)
    return True, f"FQDN '{own_domain}' is unique across projects on this node"


def check_port_conflict(projects_base: str) -> tuple[bool, str]:
    """Check monitoring.host_port uniqueness across projects.

    ▶ ┌projects_base┐ → ○ scan ai-platform.yaml → ○ port_map → ⎋ (ok, message)
    """
    base = Path(projects_base)
    if not base.is_dir():
        return True, f"PROJECTS_BASE ({base}) not available — skipping port check"

    logger.info("[IMP:7][ports] Checking port uniqueness across %s...", base)

    port_map: dict[int, str] = {}
    for yaml_file in sorted(base.glob("*/ai-platform.yaml")):
        project_name = yaml_file.parent.name
        try:
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError) as e:
            logger.warning("[IMP:7][ports] Cannot parse %s: %s", yaml_file, e)
            continue
        if not isinstance(data, dict):
            continue
        monitoring = data.get("monitoring", {})
        host_port = monitoring.get("host_port", 0) if isinstance(monitoring, dict) else 0
        if not host_port:
            continue
        try:
            host_port = int(host_port)
        except (TypeError, ValueError):
            continue
        if host_port in port_map:
            msg = f"Port conflict: {host_port} claimed by '{project_name}' and '{port_map[host_port]}'"
            logger.error("[IMP:10][ports] %s", msg)
            return False, msg
        port_map[host_port] = project_name
        logger.info("[IMP:6][ports]   Port %s → %s", host_port, project_name)

    logger.info("[IMP:9][ports] All host ports are unique across projects")
    return True, "All host ports are unique across projects"


def _resolve_projects_base() -> str:
    """Resolve PROJECTS_BASE: env → <repo>/projects → "" (skip)."""
    import os

    env = os.environ.get("PROJECTS_BASE", "")
    if env:
        return env
    script_dir = Path(__file__).resolve().parent
    fallback = script_dir / ".." / ".." / "projects"
    return str(fallback) if fallback.is_dir() else ""


def build_parser() -> argparse.ArgumentParser:
    """CLI parser: --check-fqdn <project-dir> | --check-ports [projects-base]."""
    parser = argparse.ArgumentParser(description="FQDN / host_port conflict checks between projects")
    subparsers = parser.add_subparsers(dest="command", required=True)
    fqdn = subparsers.add_parser("check-fqdn", help="Check FQDN uniqueness for a project dir")
    fqdn.add_argument("project_dir", help="Project directory to check")
    fqdn.add_argument("--projects-base", default=None, help="Projects base dir (default: PROJECTS_BASE env)")
    ports = subparsers.add_parser("check-ports", help="Check host_port uniqueness across projects")
    ports.add_argument("projects_base", nargs="?", default=None, help="Projects base dir")
    return parser


def main() -> int:
    """CLI entry: exit 0 = ok, 1 = conflict/error."""
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    args = build_parser().parse_args()

    if args.command == "check-fqdn":
        ok, msg = check_fqdn_conflict(args.project_dir, args.projects_base)
    else:
        base = args.projects_base or _resolve_projects_base()
        ok, msg = check_port_conflict(base)
    print(f"[IMP:9][conflict_checks][result] {'OK' if ok else 'FAIL'}: {msg}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
