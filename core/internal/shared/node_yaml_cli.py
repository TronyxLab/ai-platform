#!/usr/bin/env python3
# GREP_SUMMARY: node_yaml_cli, CLI, argparse, get, get-many, resolve, validate, mutation, find-project, domain-config
# STRUCTURE: ▶ build_arg_parser → ◇ dispatch: --resolve (no file) | --get | --get-many | --domain-config | --find-project | --validate | --validate-schema | mutation (add/remove/update) → ⎋ exit code
# region MODULE_CONTRACT
## @purpose  CLI entrypoint for core.internal.shared.node_yaml (DevPlan 117 G T51 extraction).
##           All 10 _cli_* functions + _build_arg_parser + main() moved verbatim from
##           node_yaml.py (L1459-1889, ~430 LOC). NodeYaml class stays in node_yaml.py.
## @scope    Invoked only via `python3 -m core.internal.shared.node_yaml` (shell facades) or
##           `python3 -m core.internal.shared.node_yaml_cli`. No external code imports this module
##           directly — node_yaml.py lazy-imports main() for __main__ backward compatibility.
##           Волна 118 B3: --typed-* флаги и _cli_typed_json удалены вместе с typed-геттерами
##           node_yaml.py (0 потребителей, verify-then-delete). --domain-config сохранён
##           (get_domain_config — потребитель preflight.py).
## @invariants
##   Exit codes: 0=success, 1=not found/generic, 2=ConfigNotFoundError,
##   3=ConfigParseError, 4=ConfigValidationError, 10=PlatformFatalError.
##   --get with missing key exits 1 (not 4) for shell || compatibility.
##   --get-many: empty/malformed spec → ConfigValidationError (exit 4); missing key → empty value (exit 0).
##   Scalar output contract (DevPlan 123 T6 — единая точка нормализации): bool → lowercase
##   "true"/"false" (НЕ Python "True"/"False"), числа (int/float) → десятичные строки str(value),
##   прочие типы (str/None/list/dict) — как есть (str()/Python repr). JSON-режимы
##   (--items, --json-output) возвращают СЫРЫЕ Python-типы (json.dumps — НЕ нормализуются).
##   --resolve: stdout contains EXACTLY ONE line (the resolved path) — shell $() consumers.
##   --file is NOT argparse-required: --resolve (3-path search) legitimately runs without it.
## @rationale  DevPlan 117 G T51 — CLI is fully DI-isolated (all functions take NodeYaml as a parameter,
##            never self), extraction is safe. python3 -m core.internal.shared.node_yaml keeps working
##            via lazy main() import in node_yaml.py (AC-G5, R6 mitigation).
## @changes  2026-08-01 · DevPlan 117 G T51 — extracted from node_yaml.py
# endregion MODULE_CONTRACT

import argparse
import json
import logging
import sys
from typing import Any

from core.internal.shared.exceptions import (
    ConfigNotFoundError,
    ConfigParseError,
    ConfigValidationError,
    PlatformFatalError,
)
from core.internal.shared.node_yaml import NodeYaml, ProjectEntry

logger = logging.getLogger(__name__)


# region FUNC_cli
## @purpose  CLI entrypoint for shell consumers. python3 -m core.internal.shared.node_yaml [args]
## @io — ⇥ sys.argv → ⎋ sys.exit(code)
## @complexity — O(N) YAML parse + O(K) for operations
## @invariants
##   Exit codes: 0=success, 1=not found/generic, 2=ConfigNotFoundError,
##   3=ConfigParseError, 4=ConfigValidationError, 10=PlatformFatalError.
##   --get with missing key exits 1 (not 4) for shell || compatibility.
def _build_arg_parser() -> argparse.ArgumentParser:
    """Build argument parser for the NodeYaml CLI.

    ## @purpose  Centralized argparse construction for testability.
    ## @io — ⎋ argparse.ArgumentParser
    ## @complexity — O(1)
    """
    parser = argparse.ArgumentParser(description="NodeYaml unified facade CLI")
    # --file is NOT argparse-required: --resolve (3-path search) legitimately runs without it.
    # Runtime enforcement in main(): all other operations print help when --file is absent.
    # ⚠️ TRAP[BUG] · 2026-07-31 · P1 · --resolve unreachable: --file required=True rejected
    # · Symptom: `python3 -m core.internal.shared.node_yaml --resolve --resolve-node X` → argparse
    # ·   error "the following arguments are required: --file", exit 2 — _cli_resolve never ran.
    # · Root: argparse validates required args BEFORE main() dispatch; --resolve needs no --file.
    # · Fix: required=False + runtime check in main() (line ~1604: `if not args.file`).
    # · Prevention: CLI mode-flag args (--resolve, --find-project) must not require --file at parse time.
    parser.add_argument("--file", required=False, help="Path to node.yaml")
    parser.add_argument("--get", help="Dotted key to retrieve (e.g., node.host)")
    parser.add_argument(
        "--get-many",
        help="Batch extraction (DevPlan 116 B3 T5, U-52): comma-separated alias:dotted-key pairs "
        "(e.g. owner_key:node.owner_key,context0:contexts.0.name). Output: alias<TAB>value lines; "
        "missing key → empty value (exit 0); malformed/empty spec → ConfigValidationError (exit 4).",
    )
    parser.add_argument("--default", help="Default value if key not found")
    parser.add_argument("--items", action="store_true", help="Output list as JSON array")
    parser.add_argument("--domain-config", action="store_true", help="Output domain config as field:value lines")
    parser.add_argument(
        "--format",
        choices=["field:value", "lines"],
        default="field:value",
        help="Output format for --domain-config (DevPlan 118 E12): "
        "'field:value' = legacy field:value lines (default), "
        "'lines' = bare values each on own line in fixed order "
        "(platform_domain, email, acme_dns_plugin, project_domains) — "
        "replaces shell grep|cut re-parsing (issue-cert.sh:600-619, D18).",
    )
    parser.add_argument("--json-output", action="store_true", help="Output entire YAML document as JSON")
    parser.add_argument("--find-project", help="Find project by name and output JSON + org + host")
    parser.add_argument("--context", action="store_true", help="Output context name")

    # DevPlan 088 T2: resolve
    parser.add_argument("--resolve", action="store_true", help="Resolve node.yaml via 3-path search")
    parser.add_argument("--resolve-node", help="Node name for --resolve")

    # DevPlan 088 T3: jsonschema validation
    parser.add_argument("--validate-schema", action="store_true", help="Validate node.yaml against JSON schema")
    parser.add_argument("--schema-path", help="Path to JSON schema file for --validate-schema")

    # DevPlan 088 T1/T3.5: typed output — УДАЛЕНО (волна 118 B3): typed-геттеры
    # (get_contexts/get_tor_config/get_repos/get_node_declaration/get_firewall/
    # get_secrets_config/get_email/get_domain/get_acme_dns_plugin/
    # get_postgres_init_databases) удалены из node_yaml.py — 0 потребителей
    # (verify-then-delete). CLI-флаги --typed-* удалены вместе с ними (R5: unknown flag → exit≠0).

    # DevPlan 088 T3.5: mutation API
    parser.add_argument(
        "--add-project",
        type=str,
        nargs=6,
        metavar=("NAME", "REPO", "TYPE", "DOMAIN", "DATABASE", "CONTEXT"),
        help="Add project: name repo type domain database context (use - for empty)",
    )
    parser.add_argument("--remove-project", help="Remove project by name")
    parser.add_argument(
        "--update-project",
        type=str,
        nargs="+",
        help="Update project: name key=value ... (e.g. myapp domain=new.example.com)",
    )

    # Legacy
    parser.add_argument("--validate", action="store_true", help="Validate node.yaml structure (basic checks)")
    return parser


def _format_cli_value(value: Any) -> str:
    """Normalize a node.yaml value for --get / --get-many scalar output.

    ## @purpose  Единая точка нормализации вывода CLI (DevPlan 123 T6). Python-bool →
    ##            lowercase "true"/"false" (раньше print() давал "True" — ломал shell
    ##            `== "true"` сравнения, TRAP[BUG] node-lifecycle.sh:53 2026-08-03);
    ##            int/float → десятичная строка (числа без кавычек); прочие типы
    ##            (str/None/list/dict) — str()/Python repr как раньше. JSON-режимы
    ##            (--items/--json-output) этот хелпер НЕ используют — сырые типы.
    ## @io — ⇥ value: Any → ⎋ str (CLI-безопасное представление)
    ## @complexity — O(1)
    ## @invariants
    ##   - isinstance(value, bool) проверяется ПЕРЕД (int, float) — bool является subclass int
    ##   - bool True → "true", False → "false" (никогда "True"/"False")
    ##   - int/float → str(value) (напр. 3 → "3", 2.5 → "2.5")
    ##   - str/None/list/dict → str(value) (совпадает с прежним print()-поведением)
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value)


def _cli_get(node: NodeYaml, args: argparse.Namespace) -> int:
    """Handle --get CLI operation.

    ## @purpose  Execute --get with optional --default and --items.
    ##            Scalar output проходит через _format_cli_value (DevPlan 123 T6): булевы →
    ##            lowercase "true"/"false", числа → десятичные строки; --items (JSON) —
    ##            сырые Python-типы без нормализации.
    ## @io — ⇥ node: NodeYaml, args → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    try:
        value = node.get(args.get, default=args.default) if args.default is not None else node.get(args.get)
    except ConfigValidationError:
        # Missing key without default → exit 1 for shell || compatibility
        print(f"Key not found: {args.get}", file=sys.stderr)
        return 1

    if args.items:
        print(json.dumps(value, indent=2) if isinstance(value, (list, dict)) else json.dumps([value]))
    else:
        print(_format_cli_value(value))
    return 0


def _traverse_dotted_list_aware(data: dict, key: str) -> Any:
    """Traverse a dotted key supporting numeric list indices (e.g. contexts.0.name).

    ## @purpose — Batch traversal for --get-many (DevPlan 116 B3 T5, U-52). The standard
    ##            NodeYaml.get() only traverses dicts; the batch spec uses `contexts.0.name`
    ##            (list-of-dicts contexts array). Missing key / non-dict / non-list / index
    ##            out of range → ConfigValidationError (caller degrades to empty value, exit 0).
    ## @io — ⇥ data: dict, key: str → ⎋ Any (value at dotted path)
    ## @complexity — O(D) where D = dot-separated segments
    ## @invariants
    ##   - dict segment → key lookup; list segment → numeric index (isdigit)
    ##   - Any traversal failure raises ConfigValidationError — never IndexError/TypeError
    """
    current: Any = data
    for part in key.split("."):
        if isinstance(current, dict):
            if part not in current:
                raise ConfigValidationError(f"Key not found: {key} (missing '{part}')")
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            idx = int(part)
            if idx >= len(current):
                raise ConfigValidationError(f"List index out of range: {key} (index {idx})")
            current = current[idx]
        else:
            raise ConfigValidationError(f"Cannot traverse into non-dict at '{part}' for key '{key}'")
    return current


def _cli_get_many(node: NodeYaml, spec: str) -> int:
    """Handle --get-many CLI operation (batch extraction, DevPlan 116 B3 T5, U-52).

    ## @purpose  Batch extract multiple node.yaml fields in ONE python3 process — replaces
    ##            N per-field --get calls in shell (bootstrap.sh 6 → 1). Spec format:
    ##            alias:dotted.key,alias2:dotted.key2 (comma-separated). Output:
    ##            lines `alias<TAB>value` — TAB separator (values may contain spaces/=).
    ##            Missing key → line `alias<TAB>` (empty value, exit 0 — shell-compatible,
    ##            mirrors --default ""). Malformed/empty spec → ConfigValidationError (exit 4,
    ##            fail-fast — the shell must not silently proceed with a broken extraction).
    ## @io — ⇥ node: NodeYaml, spec: str → ⎋ exit_code: int (0 always on valid spec)
    ## @complexity — O(K * D) where K = spec entries, D = dotted-key depth
    ## @invariants
    ##   - Empty/whitespace spec → ConfigValidationError (exit 4)
    ##   - Entry without ':' → ConfigValidationError (exit 4)
    ##   - Missing key or non-dict traversal → empty value (exit 0), like --default ""
    ##   - stdout carries ONLY alias<TAB>value lines — machine-parseable by `while IFS=$'\t' read`
    ##   - Значения проходят _format_cli_value (DevPlan 123 T6): bool → "true"/"false",
    ##     числа → str; пустая строка (missing key) печатается как есть (alias<TAB>)
    """
    if not spec or not spec.strip():
        logger.error("[IMP:10][NodeYaml._cli_get_many] Empty --get-many spec")
        raise ConfigValidationError("--get-many spec is empty (expected alias:key,alias2:key2)")

    pairs: list[tuple[str, str]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            logger.error("[IMP:10][NodeYaml._cli_get_many] Malformed spec entry: %r", part)
            raise ConfigValidationError(f"--get-many malformed entry (expected alias:key): {part}")
        alias, key = part.split(":", 1)
        pairs.append((alias.strip(), key.strip()))

    if not pairs:
        logger.error("[IMP:10][NodeYaml._cli_get_many] Empty --get-many spec (no valid entries)")
        raise ConfigValidationError("--get-many spec is empty (expected alias:key,alias2:key2)")

    for alias, key in pairs:
        try:
            value = _traverse_dotted_list_aware(node.raw(), key)
        except ConfigValidationError:
            # Missing key / non-dict traversal → empty value (exit 0), shell-compatible
            value = ""
        print(f"{alias}\t{_format_cli_value(value)}")

    logger.info("[IMP:9][NodeYaml._cli_get_many] Batch-extracted %d field(s)", len(pairs))
    return 0


def _cli_domain_config(node: NodeYaml, args: argparse.Namespace) -> int:
    """Handle --domain-config CLI operation.

    ## @purpose  Output domain config as field:value lines for shell parsing.
    ##            --format lines (DevPlan 118 E12): bare values each on own line in
    ##            fixed order (platform_domain, email, acme_dns_plugin, project_domains)
    ##            — replaces shell grep|cut re-parsing (issue-cert.sh:600-619, D18).
    ## @io — ⇥ node: NodeYaml → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    cfg = node.get_domain_config()
    if args.format == "lines":
        # Bare values each on own line — mapfile-safe (issue-cert.sh, D18)
        print(cfg.platform_domain)
        print(cfg.email)
        print(cfg.acme_dns_plugin)
        print(" ".join(cfg.project_domains))
        return 0
    print(f"platform_domain:{cfg.platform_domain}")
    print(f"email:{cfg.email}")
    print(f"acme_dns_plugin:{cfg.acme_dns_plugin}")
    print(f"project_domains:{' '.join(cfg.project_domains)}")
    return 0


def _cli_find_project(node: NodeYaml, project_name: str) -> int:
    """Handle --find-project CLI operation.

    ## @purpose  Find project by name, output JSON + org + host for shell scripts.
    ## @io — ⇥ node: NodeYaml, project_name: str → ⎋ exit_code: int
    ## @complexity — O(P) where P = number of projects
    """
    projects = node.get_projects()
    for proj in projects:
        if isinstance(proj, dict) and proj.get("name") == project_name:
            print(json.dumps(proj, indent=2))
            ctx = node.get_context()
            if ctx:
                print(f"___ORG___{ctx}")
            nfo = node.get_node_info()
            if nfo.fqdn:
                print(f"___HOST___{nfo.fqdn}")
            elif node.get("node.host", default=""):
                print(f"___HOST___{node.get('node.host')}")
            return 0
    print(f"Project not found: {project_name}", file=sys.stderr)
    return 1


def _cli_validate(node: NodeYaml) -> int:
    """Handle --validate CLI operation (basic checks).

    ## @purpose  Validate node.yaml structure, output errors to stderr.
    ## @io — ⇥ node: NodeYaml → ⎋ exit_code: int
    ## @complexity — O(1) after load
    """
    errors = node.validate()
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return len(errors)


def _cli_validate_schema(node: NodeYaml, schema_path: str | None = None) -> int:
    """Handle --validate-schema CLI operation with jsonschema.

    ## @purpose  Validate node.yaml against JSON schema, output errors to stderr.
    ## @io — ⇥ node: NodeYaml, schema_path: Optional[str] → ⎋ exit_code: int
    ## @complexity — O(N) for YAML parse + O(S) for jsonschema
    """
    errors = node.validate(schema_path=schema_path)
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return len(errors)


def _cli_resolve(args: argparse.Namespace) -> int:
    """Handle --resolve CLI operation.

    ## @purpose  Resolve node.yaml via 3-path search and print path.
    ## @io — ⇥ args → ⎋ exit_code: int
    ## @complexity — O(P) for search + O(N) for YAML parse
    ## @invariants
    ##   - stdout contains EXACTLY ONE line: the resolved node.yaml path.
    ##     Shell consumers do `path="$(python3 -m ... --resolve ...)"` — multi-line
    ##     stdout would corrupt NODE_YAML_PATH (path + marker lines).
    """
    try:
        resolved = NodeYaml.resolve(node_name=args.resolve_node)
        print(resolved._path)
        return 0
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2


def main() -> int:
    """NodeYaml CLI entrypoint.

    ## @purpose  Main entry for python3 -m core.internal.shared.node_yaml [args]
    ## @io — ⇥ sys.argv → ⎋ sys.exit(code)
    ## @complexity — O(1) dispatch
    """
    parser = _build_arg_parser()
    args = parser.parse_args()

    # --resolve does not need --file
    if args.resolve:
        return _cli_resolve(args)

    # All other operations need --file
    if not args.file:
        parser.print_help()
        return 0

    try:
        node = NodeYaml(args.file)
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except ConfigParseError as e:
        print(str(e), file=sys.stderr)
        return 3

    try:
        if args.get:
            return _cli_get(node, args)
        if args.get_many:
            return _cli_get_many(node, args.get_many)
        if args.domain_config:
            return _cli_domain_config(node, args)
        if args.context:
            print(node.get_context())
            return 0
        if args.json_output:
            print(json.dumps(node.raw(), indent=2))
            return 0
        if args.find_project:
            return _cli_find_project(node, args.find_project)
        if args.validate:
            return _cli_validate(node)
        if args.validate_schema:
            return _cli_validate_schema(node, schema_path=args.schema_path)
        if args.add_project:
            name, repo, ptype, domain, database, context = args.add_project
            project = ProjectEntry(
                name=name,
                repo=repo,
                type=ptype,
                domain=domain if domain != "-" else "",
                database=database if database != "-" else "",
                context=context if context != "-" else "",
            )
            node.add_project(project)
            print(f"Added project: {name}")
            return 0
        if args.remove_project:
            removed = node.remove_project(args.remove_project)
            if removed:
                print(f"Removed project: {args.remove_project}")
                return 0
            print(f"Project not found: {args.remove_project}", file=sys.stderr)
            return 1
        if args.update_project:
            if len(args.update_project) < 2:
                print("Usage: --update-project name key=value [key=value ...]", file=sys.stderr)
                return 1
            name = args.update_project[0]
            updates: dict[str, str] = {}
            for kv in args.update_project[1:]:
                if "=" not in kv:
                    print(f"Invalid key=value pair: {kv}", file=sys.stderr)
                    return 1
                k, v = kv.split("=", 1)
                updates[k] = v
            updated = node.update_project(name, **updates)
            if updated:
                print(f"Updated project: {name} ({', '.join(updates.keys())})")
                return 0
            print(f"Project not found: {name}", file=sys.stderr)
            return 1
        parser.print_help()
        return 0
    except ConfigNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 2
    except ConfigParseError as e:
        print(str(e), file=sys.stderr)
        return 3
    except ConfigValidationError as e:
        print(str(e), file=sys.stderr)
        return 4
    except PlatformFatalError as e:
        print(str(e), file=sys.stderr)
        return 10
    return 0


# endregion FUNC_cli


if __name__ == "__main__":
    sys.exit(main())
