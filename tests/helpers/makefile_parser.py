# GREP_SUMMARY: makefile_parser, shared-test-helper, extract-targets, phony, include-chains, gate-helper
# STRUCTURE: ▶ extract_makefile_targets ┐
#           ▶ get_all_targets ──────────┤ → ⊕ Makefile target extraction → ⎋ shared for gates
# region MODULE_CONTRACT
## @purpose  Shared Makefile target parsers for gate tests (DevPlan 171 W1.7) — единая
##           реализация вместо дублей в test_gate_no_unregistered_entrypoint.py
##           (extract_makefile_targets) и test_gate_manifest_integrity.py (get_all_targets).
## @scope    tests/helpers/ — импортируется только gate-тестами и их unit-тестами.
## @invariants
##   - extract_makefile_targets: построчный парсер (без include-резолва) — реальные
##     таргеты, пропуская .PHONY-декларации, переменные (:=, =, ?=, +=), ALL_CAPS-стабы
##   - get_all_targets: .PHONY + явные таргеты + include-цепочки (флаг include_chains)
##   - Никакого file I/O кроме чтения Makefile — детерминированные pure-функции
## @rationale Два гейта держали 2 копии парсеров с разными баг-фиксами (дрейф семантики).
##            Один shared-модуль = один канон, unit-тест + negative-тест ловят инжекции.
## @changes  2026-08-15 | DevPlan 171 W1.7 — created (extracted from gates verbatim)
# endregion MODULE_CONTRACT

from __future__ import annotations

import os
import pathlib
import re
from pathlib import Path

__all__ = ["extract_makefile_targets", "get_all_targets"]


# region FUNC_extract_makefile_targets
def extract_makefile_targets(makefile_path: str | os.PathLike[str]) -> list[str]:
    """Extract declared (non-`.PHONY`, non-variable) targets from a Makefile.

    ## @purpose  Parse a Makefile and return all real target names, skipping
    ##            `.PHONY` declarations, variable assignments (``:=``, ``=``, ``?=``,
    ##            ``+=``), and bare ALL_CAPS variable names.
    ## @io        ⇥ makefile_path: str → ⎋ list[str] of target names
    ## @complexity  O(L) where L = number of lines in the Makefile
    ## @invariants
    ##   - Lines starting with ``#``, ``.``, or ``include`` are ignored
    ##   - Variable assignments (rest starts with ``=``, ``:=``, ``?=``, ``+=``) are skipped
    ##   - Bare ALL_CAPS names without dependencies are skipped (variable stubs)
    ##   - Target aliases (``target: dependency ## desc``) are captured
    """
    targets: list[str] = []
    target_re = re.compile(r"^([a-zA-Z0-9][a-zA-Z0-9_-]*)\s*:\s*(.*)$")

    with pathlib.Path(makefile_path).open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()

            # Skip empty, comment, directive, and include lines
            if not line or line.startswith(("#", ".", "include")):
                continue

            m = target_re.match(line)
            if not m:
                continue

            name = m.group(1)
            rest = m.group(2).strip()

            # Skip Make variable assignments: VAR := val, VAR = val, VAR ?= val, VAR += val
            if rest and re.match(r"^[:?+]?=", rest):
                continue

            # Skip bare ALL_CAPS names (potentially a variable stub)
            if re.match(r"^[A-Z_]+$", name) and not rest:
                continue

            targets.append(name)

    return targets


# endregion FUNC_extract_makefile_targets


# region FUNC_extract_phony_targets
def _extract_phony_targets(text: str) -> set[str]:
    """Extract target names from .PHONY: declarations in Makefile text."""
    targets: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(".PHONY:"):
            parts = stripped[len(".PHONY:") :].strip().split()
            for part_raw in parts:
                part = part_raw.strip()
                if part and not part.startswith("$"):
                    targets.add(part)
    return targets


# endregion FUNC_extract_phony_targets


# region FUNC_extract_explicit_targets
def _extract_explicit_targets(text: str) -> set[str]:
    """Extract target names from explicit Makefile target definitions."""
    targets: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*[:?+]?=", stripped):
            continue
        match = re.match(r"^([a-zA-Z0-9_.\-]+)\s*:", stripped)
        if match:
            target = match.group(1)
            if target.startswith("."):
                continue  # Skip special make variables like .DEFAULT_GOAL, .PHONY, etc.
            if not target.startswith("$") and target != ".PHONY":
                targets.add(target)
    return targets


# endregion FUNC_extract_explicit_targets


# region FUNC_read_included_contents
def _read_included_contents(filepath: str | os.PathLike[str], depth: int = 0) -> list[str]:
    """Recursively read content of Makefiles referenced via `include` directives.

    ## @purpose — Follow include directives to resolve template targets.
    ## @io — ⇥ filepath: str, depth: int → ⎋ list[str] of included file contents
    ## @complexity — O(n * d) where n = lines, d = include depth
    """
    if depth > 5:
        return []
    if not pathlib.Path(filepath).is_file():
        return []
    with pathlib.Path(filepath).open(encoding="utf-8") as f:
        text = f.read()
    contents: list[str] = []
    makefile_dir = Path(filepath).parent
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("include "):
            inc_rel = stripped[len("include ") :].strip()
            inc_path = os.path.normpath(Path(makefile_dir) / inc_rel)
            if pathlib.Path(inc_path).is_file():
                with pathlib.Path(inc_path).open(encoding="utf-8") as inc_f:
                    contents.append(inc_f.read())
                contents.extend(_read_included_contents(inc_path, depth + 1))
    return contents


# endregion FUNC_read_included_contents


# region FUNC_get_all_targets
def get_all_targets(filepath: str | os.PathLike[str], *, include_chains: bool = True) -> set[str]:
    """Get all declared and explicit target names from a Makefile, optionally following includes.

    ## @purpose — Unified extraction combining .PHONY declarations, explicit target
    ##            definitions, and (при include_chains=True) targets inherited from
    ##            included template Makefiles.
    ## @io — ⇥ filepath: str, include_chains: bool → ⎋ set[str] of all target names
    """
    with pathlib.Path(filepath).open(encoding="utf-8") as f:
        text = f.read()
    targets: set[str] = _extract_phony_targets(text)
    targets |= _extract_explicit_targets(text)
    if include_chains:
        for inc_text in _read_included_contents(filepath):
            targets |= _extract_phony_targets(inc_text)
            targets |= _extract_explicit_targets(inc_text)
    return targets


# endregion FUNC_get_all_targets
