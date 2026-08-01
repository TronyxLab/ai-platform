#!/usr/bin/env python3
# GREP_SUMMARY: scaffold normalize-new-project-args positional named bridge PLATFORM_ORG PLATFORM_DEFAULT_NODE
# STRUCTURE: ▶ normalize_new_project_args(raw, org_default, node_default) → ⊕ args → ▶ main() print space-joined → ⎋ exit 0
# region MODULE_CONTRACT
## @purpose  Positional→named bridge for `make new-project`. Extracts the CLI
##           normalization logic from core/entrypoints/scaffold.sh (positional
##           args → --name/--template, injection of --org/--node env defaults)
##           into a pure Python function (DevPlan 117 Brief H D61).
## @scope    Called from scaffold.sh new-project|add-project|project branch via
##           `python3 -m core.internal.scaffold.normalize_new_project_args "$@"`.
##           stdout: normalized args space-joined; stderr: LDD logs.
## @invariants
##   - Position 0 → --name, position 1 → --template; extra positionals pass through
##   - --* flags pass through unchanged
##   - --org injected from PLATFORM_ORG only if absent from args and non-empty
##   - --node injected from PLATFORM_DEFAULT_NODE only if absent and non-empty
##   - Exit code always 0 — absence of name/template is validated downstream by
##     add-project.sh (same contract as the shell bridge it replaces)
##   - Pure function — no side effects; sys.exit only in main()
## @rationale Strangler Tier-1 extraction (DevPlan 09 §D61): argument normalization
##            is parsing business logic per language policy (Python-first). Shell
##            keeps case/esac pure routing.
## @changes  2026-08-02 · Created for Brief H D61 (117 09-DevPlan §Задача 61)
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

__all__ = ["main", "normalize_new_project_args"]


# ═══════════════════════════════════════════════════════════════════
# region FUNC_normalize_new_project_args
## @purpose  Normalize positional args into --name/--template and inject env defaults.
## @param raw           List of raw CLI args (positional + flags)
## @param org_default   PLATFORM_ORG value (or "") — injected as --org if absent
## @param node_default  PLATFORM_DEFAULT_NODE value (or "") — injected as --node if absent
## @return  List[str] of normalized args: original flags/positionals then --org/--node defaults
## @rationale Mirrors the shell bridge lines 35-66 of scaffold.sh 1:1 — same
##            positional_count semantics, same has-flag detection, same ordering.
def normalize_new_project_args(
    raw: list[str],
    org_default: str = "",
    node_default: str = "",
) -> list[str]:
    """Normalize positional args into named --name/--template plus env defaults."""
    args: list[str] = []
    positional_count = 0
    for arg in raw:
        if arg.startswith("--"):
            args.append(arg)
        elif positional_count == 0:
            args.extend(["--name", arg])
            positional_count += 1
        elif positional_count == 1:
            args.extend(["--template", arg])
            positional_count += 1
        else:
            args.append(arg)
            positional_count += 1

    if "--org" not in args and org_default:
        args.extend(["--org", org_default])
    if "--node" not in args and node_default:
        args.extend(["--node", node_default])

    logger.info(
        "[IMP:9][normalize_new_project_args] Bridge: positional→named → %s",
        " ".join(args),
    )
    return args


# endregion FUNC_normalize_new_project_args


# ═══════════════════════════════════════════════════════════════════
# region FUNC_main
## @purpose  CLI entry point — prints normalized args to stdout, always exits 0.
## @io       stdin: positional args + env (PLATFORM_ORG, PLATFORM_DEFAULT_NODE)
##           stdout: normalized args space-joined (`--name X --template Y --org Z --node W`)
##           stderr: LDD logs
## @exitcode 0  Always — downstream validation is add-project.sh's responsibility
def main() -> int:
    """CLI entry point for normalize_new_project_args.py."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s][%(name)s] %(message)s",
        stream=sys.stderr,
    )
    args = normalize_new_project_args(
        sys.argv[1:],
        org_default=os.environ.get("PLATFORM_ORG", ""),
        node_default=os.environ.get("PLATFORM_DEFAULT_NODE", ""),
    )
    print(" ".join(args))
    return 0


# endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
