#!/usr/bin/env python3
# GREP_SUMMARY: generate_agents_md, canon-table, forbidden-lists, inject-into-md, AGENTS.md-generator, CI
# STRUCTURE: ▶ load manifest → ◇ generate_canon_table(sections) → ◇ generate_forbidden_lists(forbidden_*) → ⊕ inject_into_md(md, marker, content) → ⎋ AGENTS.md with generated regions
# region MODULE_CONTRACT
## @purpose  Generator for core/AGENTS.md — produces canonical operations table and forbidden lists
##           from entrypoint-manifest.yaml, injects between <!-- GENERATED:START:<marker> --> and
##           <!-- GENERATED:END:<marker> --> markers.
## @scope    Used by `make generate-manifests` (Wave 3 of DevPlan 051). Run as CLI.
## @invariants
##   - generate_canon_table produces Markdown table rows from deploy:/bootstrap:/build:/validate:/test:
##     scaffold:/secrets:/lifecycle:/provision:/dev sections with signature+operation_ru if available
##   - generate_forbidden_lists produces Markdown lists from forbidden_* sections
##   - inject_into_md replaces content between markers, preserving marker lines
##   - If no marker found, appends markers + content at end of file
##   - Generated regions are idempotent — re-running replaces previous content
## @rationale DevPlan 051 Wave 3: automated AGENTS.md table generation eliminates manual sync
##            between entrypoint-manifest.yaml and AGENTS.md canonical operations table.
##            Forbidden lists also generated to ensure parity.
## @see      core/AGENTS.md — target file with generated sections
## @see      core/entrypoint-manifest.yaml — source file
## @changes 2026-07-22 | Created (DevPlan 051 Wave 3)
##           2026-07-30 | Added --check mode: in-memory injection + byte comparison, exit 0/1
##           Refactored inject_into_md → _inject_content (string-based) + inject_into_md (file wrapper)
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import difflib
import logging
import sys
from pathlib import Path

import yaml

# endregion IMPORTS

# region CONSTANTS

logger = logging.getLogger(__name__)

# Sections that contribute to the canonical operations table
TABLE_SECTIONS: tuple[str, ...] = (
    "bootstrap",
    "deploy",
    "build",
    "validate",
    "test",
    "test/gate",
    "scaffold",
    "secrets",
    "lifecycle",
    "provision",
    "dev",
    "repair",
)

# endregion CONSTANTS


# region PUBLIC_API


def generate_canon_table(manifest: dict) -> str:
    """Generate Markdown table rows from canonical operations sections.

    ## @purpose  Generate Markdown table rows for AGENTS.md canonical operations table.
    ##            Iterates deploy:/bootstrap:/build:/validate:/test:/scaffold:/secrets:
    ##            /lifecycle:/provision:/dev sections.
    ##            Uses signature and operation_ru fields if available.
    ## @io       ⇥ manifest: dict — parsed entrypoint-manifest.yaml
    ##           → ⎋ str: Markdown table rows (one per target)
    ## @complexity O(T) where T = total targets across all relevant sections
    ## @invariants
    ##   - Each row format: | `make {target}` | {operation_ru or description} | {signature or ''} | {delegates_to} |
    ##   - Sections with make_target entries contribute rows
    ##   - module_lifecycle and lib sections are NOT included in table
    ##   - Targets missing make_target field are skipped
    """
    print("[IMP:7][generate_canon_table] Generating canonical operations table", file=sys.stderr)
    rows: list[str] = []

    # Use the structured manifest sections
    for section in TABLE_SECTIONS:
        entries = manifest.get(section, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            target = entry.get("make_target")
            if not target:
                continue

            description = entry.get("description", "")
            operation_ru = entry.get("operation_ru", description)
            signature = entry.get("signature", f"`make {target}`")
            delegates_to = entry.get("delegates_to", "")

            row = f"| `make {target}` | {operation_ru} | {signature} | {delegates_to} |"
            rows.append(row)

    print(
        f"[IMP:9][generate_canon_table] Generated {len(rows)} table rows across {len(TABLE_SECTIONS)} sections",
        file=sys.stderr,
    )
    return "\n".join(rows)


def generate_forbidden_lists(manifest: dict) -> str:
    """Generate forbidden sections Markdown from manifest forbidden_* entries.

    ## @purpose  Generate forbidden lists for AGENTS.md from manifest forbidden_* sections.
    ## @io       ⇥ manifest: dict — parsed entrypoint-manifest.yaml
    ##           → ⎋ str: Markdown forbidden sections with headers and bullet lists
    ## @complexity O(F) where F = total forbidden entries across all forbidden_* sections
    ## @invariants
    ##   - forbidden_directories → bullet list under '### Запрещённые директории'
    ##   - forbidden_scripts → bullet list under '### Запрещённые скрипты (имена)'
    ##   - forbidden_verbs → bullet list under '### Запрещённые глаголы (make-таргеты)'
    ##   - Empty sections produce no output
    """
    print("[IMP:7][generate_forbidden_lists] Generating forbidden lists", file=sys.stderr)
    parts: list[str] = []

    forbidden_dirs = manifest.get("forbidden_directories", [])
    if forbidden_dirs:
        parts.append("### Запрещённые директории")
        parts.append("")
        parts.append("Директории, в которых **НЕ ДОЛЖНЫ** находиться исполняемые скрипты:")
        parts.append("")
        parts.extend(f"- `{d}`" for d in forbidden_dirs)
        parts.append("")

    forbidden_scripts = manifest.get("forbidden_scripts", [])
    if forbidden_scripts:
        parts.append("### Запрещённые скрипты (имена)")
        parts.append("")
        parts.append("Следующие имена скриптов не должны существовать нигде в проекте:")
        parts.append("")
        parts.extend(f"- {s}" for s in forbidden_scripts)
        parts.append("")

    forbidden_verbs = manifest.get("forbidden_verbs", [])
    if forbidden_verbs:
        parts.append("### Запрещённые глаголы (make-таргеты)")
        parts.append("")
        parts.append("Следующие глаголы **ЗАПРЕЩЕНЫ** к использованию в качестве имён таргетов:")
        parts.append("")
        parts.extend(f"- `{v}`" for v in forbidden_verbs)
        parts.append("")

    result = "\n".join(parts).strip()
    print(f"[IMP:9][generate_forbidden_lists] Generated {len(parts)} lines of forbidden content", file=sys.stderr)
    return result


def _inject_content(content: str, marker: str, new_content: str) -> str:
    """Inject generated content between GENERATED markers in a string copy.

    ## @purpose  Core injection logic — works on a string, returns modified string.
    ##            Used by both inject_into_md (disk) and --check (in-memory).
    ## @io        ⇥ content: original markdown content, marker: marker name,
    ##             new_content: content to inject between markers
    ##           → ⎋ str: content with injected text
    ## @complexity O(L) where L = number of lines in content
    ## @invariants
    ##   - Marker lines are preserved
    ##   - Content between markers is fully replaced
    ##   - If markers missing, appended at end
    """
    start_tag = f"<!-- GENERATED:START:{marker} -->"
    end_tag = f"<!-- GENERATED:END:{marker} -->"

    start_idx = content.find(start_tag)
    end_idx = content.find(end_tag)

    if start_idx == -1 or end_idx == -1:
        # Markers not found — append at end
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n{start_tag}\n"
        content += new_content
        if not new_content.endswith("\n"):
            content += "\n"
        content += f"{end_tag}\n"
    else:
        # Replace content between markers (exclusive)
        before = content[: start_idx + len(start_tag)]
        after = content[end_idx:]

        if not before.endswith("\n"):
            before += "\n"

        content = before + new_content
        if not new_content.endswith("\n"):
            content += "\n"
        content += after

    return content


def inject_into_md(md_path: str, marker: str, new_content: str) -> None:
    """Replace content between <!-- GENERATED:START:marker --> and <!-- GENERATED:END:marker -->.

    ## @purpose  Inject generated content into AGENTS.md between marker comments.
    ##            If markers missing, appends them with content at end of file.
    ##            Delegates to _inject_content for string manipulation.
    ## @io        ⇥ md_path: path to markdown file
    ##           ⇥ marker: marker name (e.g., "canon-operations")
    ##           ⇥ new_content: string to inject between markers
    ##           → ⎋ None (side-effect: modifies file)
    ## @complexity O(L) where L = number of lines in file
    ## @invariants
    ##   - Marker lines are preserved (<!-- GENERATED:START:marker --> stays)
    ##   - Content between markers is fully replaced
    ##   - If no end marker, content appended after start marker
    ##   - If no markers at all, appended at end of file
    """
    print(f"[IMP:7][inject_into_md] Injecting content into {md_path} for marker '{marker}'", file=sys.stderr)
    file_path = Path(md_path)
    if not file_path.is_file():
        print(f"[IMP:1][inject_into_md] CRITICAL: File not found: {md_path}", file=sys.stderr)
        raise FileNotFoundError(f"Markdown file not found: {md_path}")

    content = file_path.read_text(encoding="utf-8")
    content = _inject_content(content, marker, new_content)
    file_path.write_text(content, encoding="utf-8")
    print(f"[IMP:9][inject_into_md] Content injected into {md_path} for marker '{marker}'", file=sys.stderr)


# endregion PUBLIC_API


# region CLI


def main() -> int:
    """CLI entrypoint for AGENTS.md generator.

    ▶ argparse → ◇ load manifest → ◇ generate_canon_table + generate_forbidden_lists
      → ⊕ inject_into_md (2 markers) → ⎋ exit 0

    ## @purpose  CLI for make generate-manifests integration.
    ## @io       ⇥ CLI args: --manifest, --agents-md, --marker (prefix for marker names)
    ##           → ⎋ exit code 0 on success, 1 on error
    ## @complexity O(T + F + L) where T=targets, F=forbidden entries, L=file lines
    """
    parser = argparse.ArgumentParser(
        prog="generate_agents_md.py",
        description="Generate core/AGENTS.md generated sections from entrypoint-manifest.yaml",
    )
    parser.add_argument(
        "--manifest",
        default="core/entrypoint-manifest.yaml",
        help="Path to entrypoint-manifest.yaml (default: core/entrypoint-manifest.yaml)",
    )
    parser.add_argument(
        "--agents-md",
        default="core/AGENTS.md",
        help="Path to core/AGENTS.md (default: core/AGENTS.md)",
    )
    parser.add_argument(
        "--marker",
        default="canon-operations",
        help="Marker prefix for GENERATED sections (default: canon-operations). "
        "Forbidden lists use marker + '-forbidden' (e.g., canon-operations-forbidden)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with existing file byte-by-byte. "
        "Never writes to disk. Exit 0 if match, 1 if divergence.",
    )
    args = parser.parse_args()

    print("[IMP:7][main] Starting AGENTS.md generation", file=sys.stderr)

    try:
        # Load manifest
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            print(f"[IMP:1][main] CRITICAL: Manifest not found at {args.manifest}", file=sys.stderr)
            return 1

        with open(str(manifest_path)) as f:
            manifest = yaml.safe_load(f)
        if manifest is None:
            manifest = {}

        # Generate canonical table
        canon_table = generate_canon_table(manifest)

        # Generate forbidden lists
        forbidden_content = generate_forbidden_lists(manifest)

        # ══════════════════════════════════════════════════════════════
        # ── CHECK MODE: in-memory injection + byte comparison ──
        # ══════════════════════════════════════════════════════════════
        if args.check:
            logger.info("[IMP:7][main][CHECK] Running check mode — comparing %s", args.agents_md)

            agents_md_path = Path(args.agents_md)
            if not agents_md_path.is_file():
                logger.error("[IMP:1][main][CHECK] AGENTS.md not found: %s", args.agents_md)
                print(f"[IMP:1][main][CHECK] File not found: {args.agents_md}", file=sys.stderr)
                return 1

            # Read existing file content
            existing_content = agents_md_path.read_text(encoding="utf-8")

            # Inject generated content into a copy (simulate what generation would produce)
            simulated_content = existing_content
            if canon_table:
                simulated_content = _inject_content(simulated_content, args.marker, canon_table)
            if forbidden_content:
                forbidden_marker = f"{args.marker}-forbidden"
                simulated_content = _inject_content(simulated_content, forbidden_marker, forbidden_content)

            # Compare byte-by-byte
            if simulated_content == existing_content:
                logger.info("[IMP:9][main][CHECK] AGENTS.md is up-to-date — exit 0")
                print("[IMP:9][main][CHECK] AGENTS.md is up-to-date — exit 0", file=sys.stderr)
                return 0

            logger.warning("[IMP:6][main][CHECK] AGENTS.md is stale — exit 1")
            print("[IMP:6][main][CHECK] AGENTS.md is stale — divergence detected", file=sys.stderr)
            diff_lines = list(
                difflib.unified_diff(
                    existing_content.splitlines(keepends=True),
                    simulated_content.splitlines(keepends=True),
                    fromfile=f"{args.agents_md} (file)",
                    tofile=f"{args.agents_md} (regenerated)",
                )
            )
            for line in diff_lines[:20]:
                print(line, end="", file=sys.stderr)
            if len(diff_lines) > 20:
                print(f"[IMP:6][check] ... truncated ({len(diff_lines) - 20} more lines)", file=sys.stderr)
            return 1

        # ══════════════════════════════════════════════════════════════
        # ── NORMAL MODE: write to disk ──
        # ══════════════════════════════════════════════════════════════

        # Inject canonical table
        if canon_table:
            inject_into_md(args.agents_md, args.marker, canon_table)

        # Inject forbidden lists
        if forbidden_content:
            forbidden_marker = f"{args.marker}-forbidden"
            inject_into_md(args.agents_md, forbidden_marker, forbidden_content)

        print(f"[IMP:9][main] AGENTS.md generation complete — {args.agents_md} updated", file=sys.stderr)
        return 0

    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        print(f"[IMP:1][main] CRITICAL: AGENTS.md generation failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
