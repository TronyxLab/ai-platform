#!/usr/bin/env python3
# GREP_SUMMARY: generate_agents_md, canon-table, glossary, inject-into-md, AGENTS.md-generator, CI
# STRUCTURE: ▶ load manifest → ◇ generate_canon_table(sections) → ◇ generate_glossary(allowed_verbs) → ⊕ inject_into_md(md, marker, content) → ⎋ AGENTS.md with generated regions
# region MODULE_CONTRACT
## @purpose  Generator for core/AGENTS.md (canonical operations table) AND
##           root AGENTS.md (glossary из allowed_verbs) — G4-расширение (DevPlan 116 B11 T3, U-45/D3).
##           Инъекция между <!-- GENERATED:START:<marker> --> и <!-- GENERATED:END:<marker> --> маркерами.
## @scope    Used by `make generate-manifests` (Wave 3 of DevPlan 051 + DevPlan 116 B11 T3).
##           --target core (default) → core/AGENTS.md; --target root → root AGENTS.md глоссарий.
## @invariants
##   - generate_canon_table produces Markdown table rows from deploy:/bootstrap:/build:/validate:/test:
##     scaffold:/secrets:/lifecycle:/provision:/dev sections with signature+operation_ru if available
##   - generate_glossary produces глоссарий-таблицу из allowed_verbs + join по make_target
##     с секциями манифеста (operation_ru/description); verbs без описания → '—' (не RED)
##   - inject_into_md replaces content between markers, preserving marker lines
##   - If no marker found, appends markers + content at end of file
##   - Generated regions are idempotent — re-running replaces previous content
##   - --target root генерирует ТОЛЬКО секцию между glossary-маркерами; ручные элементы
##     (❌-глаголы, «Правило», двухуровневая семантика) вне маркеров НЕ трогаются (D3)
## @rationale DevPlan 051 Wave 3: automated AGENTS.md table generation eliminates manual sync.
##            DevPlan 116 B11 T3 (U-45, D3): глоссарий root AGENTS.md дрейфовал (37/68) —
##            расширение G4 (НЕ новый генератор G7) делает таблицу из allowed_verbs (68 строк,
##            0 ручных правок); check-manifests G4 --check сверяет оба target бесплатно.
## @see      core/AGENTS.md — target file (target=core) with generated sections
## @see      AGENTS.md — target file (target=root) with generated glossary
## @see      core/entrypoint-manifest.yaml — source file
## @changes 2026-07-22 | Created (DevPlan 051 Wave 3)
##           2026-07-30 | Added --check mode: in-memory injection + byte comparison, exit 0/1
##           Refactored inject_into_md → _inject_content (string-based) + inject_into_md (file wrapper)
##           2026-08-01 | DevPlan 116 B11 T3 (U-45, D3) — --target {core,root}; generate_glossary
# endregion MODULE_CONTRACT

from __future__ import annotations

# region IMPORTS
import argparse
import difflib
import logging
import sys
from pathlib import Path
from typing import ClassVar, cast

import yaml

# endregion IMPORTS

# region TYPED_CONTRACTS
# W11: manifest.yaml payload boundaries — no Any (reportExplicitAny=error).
_ManifestData = dict[str, object]


# endregion TYPED_CONTRACTS

# region CONSTANTS

logger = logging.getLogger(__name__)

# Sections that contribute to the canonical operations table
DIFF_LINES_MAX: int = 20  # обрезка дифф-вывода при --check


TABLE_SECTIONS: tuple[str, ...] = (
    "bootstrap",
    "deploy",
    "build",
    "validate",
    "test",
    "test/gate",
    "scaffold",
    "practices",
    "secrets",
    "lifecycle",
    "provision",
    "dev",
    "repair",
    "loadtest",
)

# endregion CONSTANTS


# region HELPERS


def _escape_xml_tags(text: str) -> str:
    """Escape XML/HTML angle brackets for Doxygen compatibility.

    ▶ ┌text┐ → ⊕ replace(<, \\)) → ⊕ replace(>, \\)) → ⎋ escaped text

    ## @purpose  Escape angle brackets so Doxygen does not interpret <tag>
    ##            as an XML/HTML element (DevPlan 097 Wave B1).
    ##            Applied to manifest signature/delegates_to fields that
    ##            contain placeholder syntax like NODE=<name>.
    ## @io       ⇥ text: str — raw field value
    ##           → ⎋ str — value with < and > escaped as \\< and \\>
    ## @complexity O(L) where L = text length
    """
    return text.replace("<", "\\<").replace(">", "\\>")


# endregion HELPERS


# region PUBLIC_API


# region FUNC__build_visibility_map
def _build_visibility_map(manifest: _ManifestData) -> dict[str, str]:
    """Build verb → visibility map from make_target sections (default public).

    ## @purpose  План 175 W3.3: глоссарий/canon_table помечают internal-глаголы.
    ##            Join по имени make_target с секциями манифеста (visibility-поле).
    ## @io       ⇥ manifest → ⎋ dict[str, str]: verb → 'public'|'internal'
    ## @complexity O(S*E) — S секций, E записей
    ## @invariants — отсутствие поля → public; первая запись побеждает
    """
    vis_map: dict[str, str] = {}
    for section in TABLE_SECTIONS:
        entries = manifest.get(section, [])
        if not isinstance(entries, list):
            continue
        for entry in cast(list[dict[str, object]], entries):
            target = entry.get("make_target")
            if not target:
                continue
            if target in vis_map:
                continue
            vis_map[cast(str, target)] = "internal" if entry.get("visibility") == "internal" else "public"
    return vis_map


# endregion FUNC__build_visibility_map


def generate_canon_table(manifest: _ManifestData) -> str:
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
    visibility = _build_visibility_map(manifest)

    # Use the structured manifest sections
    for section in TABLE_SECTIONS:
        entries = manifest.get(section, [])
        if not isinstance(entries, list):
            continue
        # W11: list[Unknown] after isinstance → cast to typed table entries
        for entry in cast(list[dict[str, object]], entries):
            entry_typed = entry
            target = entry_typed.get("make_target")
            if not target:
                continue

            # W11: manifest fields are YAML strings → cast (no runtime coercion)
            description = cast(str, entry_typed.get("description", ""))
            operation_ru = cast(str, entry_typed.get("operation_ru", description))
            signature = cast(str, entry_typed.get("signature", f"`make {target}`"))
            delegates_to = _escape_xml_tags(cast(str, entry_typed.get("delegates_to", "")))
            # План 175 W3.3: internal-глаголы помечаются в таблице
            if visibility.get(cast(str, target)) == "internal":
                operation_ru = f"{operation_ru} (internal)"

            row = f"| `make {target}` | {operation_ru} | {_escape_xml_tags(signature)} | {delegates_to} |"
            rows.append(row)

    print(
        f"[IMP:9][generate_canon_table] Generated {len(rows)} table rows across {len(TABLE_SECTIONS)} sections",
        file=sys.stderr,
    )
    return "\n".join(rows)


def generate_glossary(manifest: _ManifestData) -> str:
    """Generate root AGENTS.md glossary table from allowed_verbs + section descriptions.

    ## @purpose  Глоссарий глаголов (root AGENTS.md) из SoT: allowed_verbs (68 имён) +
    ##            join по имени make_target с секциями манифеста (operation_ru/description).
    ##            DevPlan 116 B11 T3 (U-45, D3): расширение G4 — НЕ новый генератор.
    ## @io       ⇥ manifest: dict — parsed entrypoint-manifest.yaml
    ##           → ⎋ str: Markdown table rows (| ✅ | `verb` | операция |)
    ## @complexity O(V * S) where V = verbs, S = section entries
    ## @invariants
    ##   - Все allowed_verbs попадают в таблицу (полный список, не только ✅-задокументированные)
    ##   - Verbs без описания (нет make_target-записи с operation_ru/description) → '—' (не RED)
    ##   - 3 колонки: Статус | Глагол | Операция (формат существующей ручной таблицы)
    ##   - Порядок: сортировка по имени глагола (детерминизм для check-manifests)
    ##   - План 175 W3.3: internal-глаголы помечаются (⚙️ + суффикс '(internal)')
    """
    print("[IMP:7][generate_glossary] Generating glossary from allowed_verbs", file=sys.stderr)

    # Build verb → description map from ALL sections (join по имени таргета)
    verb_desc: dict[str, str] = {}
    for section in TABLE_SECTIONS:
        entries = manifest.get(section, [])
        if not isinstance(entries, list):
            continue
        # W11: list[Unknown] after isinstance → cast to typed table entries
        for entry in cast(list[dict[str, object]], entries):
            entry_typed = entry
            # W11: make_target is a YAML scalar → cast to str key
            target = cast(str, entry_typed.get("make_target"))
            if not target:
                continue
            desc = cast(str, entry_typed.get("operation_ru") or entry_typed.get("description") or "—")
            if target not in verb_desc:
                verb_desc[target] = desc

    allowed_verbs = manifest.get("allowed_verbs", [])
    if not isinstance(allowed_verbs, list):
        allowed_verbs = []

    # W11: list[Unknown] after isinstance → cast to str list
    allowed_verbs_typed = cast(list[str], allowed_verbs)
    visibility = _build_visibility_map(manifest)
    rows: list[str] = []
    for verb in sorted(allowed_verbs_typed):
        # План 175 W3.3: internal-глаголы помечаются (⚙️ + суффикс '(internal)')
        if visibility.get(verb) == "internal":
            rows.append(f"| ⚙️ | `{verb}` (internal) | {verb_desc.get(verb, '—')} |")
        else:
            rows.append(f"| ✅ | `{verb}` | {verb_desc.get(verb, '—')} |")

    print(
        f"[IMP:9][generate_glossary] Generated {len(rows)} glossary rows ({len(allowed_verbs_typed)} verbs)",
        file=sys.stderr,
    )
    return "\n".join(rows)


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
    ##           ⇥ marker: marker name (e.g., "canon_table")
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
        msg = f"Markdown file not found: {md_path}"
        raise FileNotFoundError(msg)

    content = file_path.read_text(encoding="utf-8")
    content = _inject_content(content, marker, new_content)
    file_path.write_text(content, encoding="utf-8")
    print(f"[IMP:9][inject_into_md] Content injected into {md_path} for marker '{marker}'", file=sys.stderr)


# endregion PUBLIC_API


# region CLI


class _AgentsMdArgs(argparse.Namespace):
    """Typed argparse namespace (W11: Namespace attribute access is Any).

    ClassVar-аннотации БЕЗ значений (только типы) — значения ломают hasattr/parser-дефолты.
    """

    target: ClassVar[str]
    manifest: ClassVar[str]
    agents_md: ClassVar[str | None]
    marker: ClassVar[str]
    check: ClassVar[bool]


def main() -> int:
    """CLI entrypoint for AGENTS.md generator.

    ▶ argparse → ◇ load manifest → ◇ generate_canon_table
      → ⊕ inject_into_md (2 markers) → ⎋ exit 0

    ## @purpose  CLI for make generate-manifests integration.
    ## @io       ⇥ CLI args: --manifest, --agents-md, --marker (prefix for marker names)
    ##           → ⎋ exit code 0 on success, 1 on error
    ## @complexity O(T + L) where T=targets, L=file lines
    """
    parser = argparse.ArgumentParser(
        prog="generate_agents_md.py",
        description="Generate AGENTS.md generated sections from entrypoint-manifest.yaml (core: canon_table; root: glossary)",
    )
    parser.add_argument(
        "--target",
        choices=("core", "root"),
        default="core",
        help="core (default): canon_table в core/AGENTS.md; "
        "root: glossary-секция в корневом AGENTS.md (DevPlan 116 B11 T3, D3)",
    )
    parser.add_argument(
        "--manifest",
        default="core/entrypoint-manifest.yaml",
        help="Path to entrypoint-manifest.yaml (default: core/entrypoint-manifest.yaml)",
    )
    parser.add_argument(
        "--agents-md",
        default=None,
        help="Path to target AGENTS.md (default: core/AGENTS.md for target=core, AGENTS.md for target=root)",
    )
    parser.add_argument(
        "--marker",
        default="canon_table",
        help="Marker prefix for GENERATED sections (default: canon_table). "
        "Root glossary uses 'glossary' (forbidden-эмиссия упразднена DevPlan 171 W3.3).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: compare generated output with existing file byte-by-byte. "
        "Never writes to disk. Exit 0 if match, 1 if divergence.",
    )
    args = parser.parse_args(namespace=_AgentsMdArgs())

    # W11: ClassVar нельзя переприсваивать на инстансе → локальная переменная (parser default: None)
    agents_md = (
        args.agents_md if args.agents_md is not None else ("AGENTS.md" if args.target == "root" else "core/AGENTS.md")
    )
    glossary_marker = "glossary" if args.target == "root" else args.marker

    print(f"[IMP:7][main] Starting AGENTS.md generation (target={args.target})", file=sys.stderr)

    # ruff: ignore[PLW0717] — try-тело содержит return-ветки с fall-through (после-try код) — извлечение небезопасно
    try:
        # Load manifest
        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            print(f"[IMP:1][main] CRITICAL: Manifest not found at {args.manifest}", file=sys.stderr)
            return 1
        with Path(str(manifest_path)).open(encoding="utf-8") as f:
            # W11: yaml.safe_load returns Any → cast to manifest boundary
            manifest = cast(_ManifestData | None, yaml.safe_load(f))
        if manifest is None:
            manifest = {}
        if args.target == "root":
            # ══════════════════════════════════════════════════════════════
            # TARGET=root: глоссарий из allowed_verbs (D3 — расширение G4)
            # ══════════════════════════════════════════════════════════════
            glossary_content = generate_glossary(manifest)

            if args.check:
                root_md_path = Path(agents_md)
                if not root_md_path.is_file():
                    logger.error("[IMP:1][main][CHECK] root AGENTS.md not found: %s", args.agents_md)
                    print(f"[IMP:1][main][CHECK] File not found: {agents_md}", file=sys.stderr)
                    return 1
                existing_content = root_md_path.read_text(encoding="utf-8")
                simulated = _inject_content(existing_content, glossary_marker, glossary_content)
                if simulated == existing_content:
                    logger.info("[IMP:9][main][CHECK][root] glossary is up-to-date — exit 0")
                    print("[IMP:9][main][CHECK][root] glossary is up-to-date — exit 0", file=sys.stderr)
                    return 0
                logger.warning("[IMP:6][main][CHECK][root] glossary is stale — exit 1")
                print("[IMP:6][main][CHECK][root] glossary divergence detected", file=sys.stderr)
                diff_lines = list(
                    difflib.unified_diff(
                        existing_content.splitlines(keepends=True),
                        simulated.splitlines(keepends=True),
                        fromfile=f"{agents_md} (file)",
                        tofile=f"{agents_md} (regenerated)",
                    )
                )
                for line in diff_lines[:20]:
                    print(line, end="", file=sys.stderr)
                if len(diff_lines) > DIFF_LINES_MAX:
                    print(
                        f"[IMP:6][check] ... truncated ({len(diff_lines) - DIFF_LINES_MAX} more lines)", file=sys.stderr
                    )
                return 1

            inject_into_md(agents_md, glossary_marker, glossary_content)
            print(f"[IMP:9][main][root] Glossary generation complete — {agents_md} updated", file=sys.stderr)
            return 0
        canon_table = generate_canon_table(manifest)
        if args.check:
            logger.info("[IMP:7][main][CHECK] Running check mode — comparing %s", agents_md)

            agents_md_path = Path(agents_md)
            if not agents_md_path.is_file():
                logger.error("[IMP:1][main][CHECK] AGENTS.md not found: %s", agents_md)
                print(f"[IMP:1][main][CHECK] File not found: {agents_md}", file=sys.stderr)
                return 1

            # Read existing file content
            existing_content = agents_md_path.read_text(encoding="utf-8")

            # Inject generated content into a copy (simulate what generation would produce)
            simulated_content = existing_content
            if canon_table:
                simulated_content = _inject_content(simulated_content, args.marker, canon_table)

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
                    fromfile=f"{agents_md} (file)",
                    tofile=f"{agents_md} (regenerated)",
                )
            )
            for line in diff_lines[:20]:
                print(line, end="", file=sys.stderr)
            if len(diff_lines) > DIFF_LINES_MAX:
                print(f"[IMP:6][check] ... truncated ({len(diff_lines) - DIFF_LINES_MAX} more lines)", file=sys.stderr)
            return 1
        if canon_table:
            inject_into_md(agents_md, args.marker, canon_table)
        print(f"[IMP:9][main] AGENTS.md generation complete — {agents_md} updated", file=sys.stderr)

    # ruff: ignore[BLE001] — top-level CLI handler for unexpected errors
    except Exception as e:  # noqa: EXC — top-level CLI handler for unexpected errors
        print(f"[IMP:1][main] CRITICAL: AGENTS.md generation failed: {e}", file=sys.stderr)
        return 1
    else:
        return 0


if __name__ == "__main__":
    sys.exit(main())


# endregion CLI
