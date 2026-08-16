"""Shell-source cross-layer linter — slim (DevPlan 163 W-D D2, M5; 881 → <200 LOC).

# GREP_SUMMARY: cross-layer linter, shell-source, layer-isolation, entrypoints, internal, modules, python3-m, direction-allowlist, gate8-invoke, makefile-contract
# STRUCTURE: ▶ scan core/**/*.{sh,Makefile} → ○ classify layer → ▶ 5 sh-import паттернов → ▶ resolve (cross_layer_vars) → ◇ direction/allowlist → ⊕ violations + Gate#8 v2 (cross_layer_gate8) + makefile contract → ⎋ lint_core()
"""
# region MODULE_CONTRACT
## @purpose  Тонкий кастомный линтер shell-source-импортов после split 881→<200 LOC (DevPlan 163 M5/W-D).
##           Python-dotted-импорты анализирует import-linter (.importlinter) — здесь их НЕТ.
## @scope    core/**/*.{sh,Makefile} слоёв entrypoints/internal/modules: 5 паттернов (source, .,
##           exec, bash/sh, python3 -m), Makefile contract. Резолвер путей — cross_layer_vars.py;
##           Gate #8 v2 (direct module calls + invoke validation) — cross_layer_gate8.py.
## @invariants  Только entrypoints/internal/modules подпадают; direction-allowlist postgres-hook D1;
##              R5-фикстуры core/modules/_gate_probe_*/ вне scope → RED; LINT-EXEMPT НЕ подавляет;
##              scan_py_file УДАЛЁН → .importlinter (parity: files/importlinter_parity.md)
## @rationale Shell-файлы невидимы grimp'у (import-linter сканирует .py) — source/. /bash/
##            python3 -m остаются кастомным линтером; Python — декларативными контрактами.
## @changes  2026-08-13 | DevPlan 163 W-D D2 — split: -scan_py_file/-py-цикл/-ShellCheck-B/-make-C → .importlinter
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

from tests.helpers.cross_layer_gate8 import _detect_direct_module_calls, _detect_invoke_calls, _validate_interfaces
from tests.helpers.cross_layer_vars import CORE_DIR, resolve_import
from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

_LAYERS = {"entrypoints": "entrypoints/", "internal": "internal/", "modules": "modules/"}
_RULES = {
    "entrypoints": {"internal", "lib"},
    "internal": {"internal", "lib", "modules"},
    "modules": {"lib", "templates"},
}
_IMPORTING = set(_LAYERS)
_MK_INCLUDES = {"../../templates/module.mk", "../../templates/module-system.mk", "../../Makefile.common"}
_NO_PATH = {
    "-c",
    "-s",
    "-i",
    "-l",
    "--login",
    "-r",
    "--restricted",
    "+o",
    "-o",
    "-n",
    "-x",
    "-e",
    "-u",
    "-p",
    "-v",
    "$?",
    "$#",
    "$$",
    "$!",
    "$@",
    "$*",
    "$-",
    "$0",
    "${?}",
    "${#}",
    "${$}",
    "${!}",
    "${@}",
    "${*}",
    "${-}",
    "${0}",
}
_ALLOWLIST = (
    (
        "modules",
        "internal",
        "core/modules/postgres/hooks/",
        "postgres-hook; shared.node_yaml by design (D1); container runtime",
    ),
)
# Re-export API (W-F unit-тесты / W-C миграция импортируют имена из cross_layer_linter)
from tests.helpers.cross_layer_vars import (  # ruff: ignore[F401]
    _collect_path_variables,
    _looks_like_path,
    _trace_variable_assignment,
)


def classify_layer(file_path: Path) -> str | None:
    """Layer entrypoints|internal|modules by core/ path prefix."""
    path_str = file_path.as_posix()
    for layer, prefix in _LAYERS.items():
        if f"core/{prefix}" in path_str:
            return layer
    return None


def _rel(source_file: Path) -> str:
    try:
        return source_file.resolve().relative_to(repo_root().resolve()).as_posix()
    except ValueError:
        return source_file.as_posix()


def _is_direction_allowlisted(source_file: Path, source_layer: str, target_layer: str) -> bool:
    """S7: (source_layer, target_layer, path_prefix) match."""
    rel = _rel(source_file)
    for src, tgt, prefix, _reason in _ALLOWLIST:
        if source_layer == src and target_layer == tgt and rel.startswith(prefix):
            logger.info("[IMP:9][lint][allowlist] %s — %s→%s allowlisted (%s)", rel, src, tgt, prefix)
            return True
    return False


def _has_exempt(lines: list[str], lineno: int) -> bool:
    return any(0 <= c < len(lines) and "# LINT-EXEMPT:" in lines[c].strip() for c in (lineno - 1, lineno - 2))


def scan_sh_file(file_path: Path, source_layer: str | None = None) -> list[tuple[int, str, bool]]:
    """Scan a .sh file: (lineno, import_path, exempt) — source/. exec bash/sh python3 -m."""
    imports: list[tuple[int, str, bool]] = []
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError as exc:
        logger.warning("[IMP:6][scan][sh] Cannot read %s: %s", file_path, exc)
        return imports
    skip_opt = source_layer == "modules"
    patterns = (
        (r"(?:^|\s)(?:source)\s+(\S+)", lambda p: p not in {'"$@"', "${@}", "$@", '".",'}),
        (r"(?:^|\s)\.\s+(\S+)", lambda p: p not in {'"$@"', "${@}", "$@", '".",'}),
        (
            r"(?:^|;|&&|\|\|)\s*exec\s+(\S+)",
            lambda p: p not in {">", ">>", "<", "2>", "2>>", ";"} and not p.startswith((">", "<")),
        ),
        (r"(?:^|\s)(?:bash|/bin/bash|sh|/bin/sh)\s+(\S+)", lambda p: p not in _NO_PATH and not p.startswith("-")),
        (r"python3\s+-m\s+(\S+)", lambda _p: True),
    )
    for i, stripped in enumerate((ln.strip() for ln in lines), 1):
        if not stripped or stripped.startswith("#"):
            continue
        exempt = _has_exempt(lines, i)
        for rx, ok in patterns:
            m = re.search(rx, stripped)
            if m:
                path = m.group(1).rstrip("\\")
                if ok(path) and not (not skip_opt and path.startswith(("/etc/", "/opt/"))) and _looks_like_path(path):
                    imports.append((i, path, exempt))
                break
    return imports


def check_violation(
    source_file: Path, lineno: int, import_path: str, import_type: str, exempt: bool, resolved: Path | None = None
) -> str | None:
    """Violation string if import breaks layer rules (LINT-EXEMPT → warning only, TASK-6C)."""
    if exempt:
        logger.warning("[IMP:7][lint][LINT-EXEMPT] %s:%d — no longer suppresses (TASK-6C).", source_file, lineno)
    source_layer = classify_layer(source_file)
    if source_layer is None or source_layer not in _IMPORTING:
        return None
    if import_type == "make":
        if source_layer == "modules" and import_path not in _MK_INCLUDES:
            return f"  {source_file}:{lineno} — [modules·make] include '{import_path}' — only module.mk allowed"
        return None
    if resolved is None:
        return None
    target_layer = classify_layer(resolved)
    if target_layer is None or target_layer in _RULES.get(source_layer, set()):
        return None
    if _is_direction_allowlisted(source_file, source_layer, target_layer):
        return None
    return f"  {source_file}:{lineno} — [{source_layer}→{target_layer}] '{import_path}' (forbidden)"


def lint_core() -> list[str]:
    """Shell-source + Gate #8 v2 + Makefile contract violations (Python → .importlinter)."""
    violations: list[str] = []
    sh_files = sorted(CORE_DIR.rglob("*.sh"))
    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING:
            continue
        for lineno, imp_path, exempt in scan_sh_file(fpath, source_layer):
            if msg := check_violation(
                fpath, lineno, imp_path, "sh", exempt, resolve_import(fpath, imp_path, source_layer)
            ):
                violations.append(msg)
    for fpath in sh_files:  # Gate #8 v2 — Phase 1: direct module calls from internal/
        if classify_layer(fpath) != "internal":
            continue
        for lineno, call_type, target in _detect_direct_module_calls(fpath):
            violations.append(f"  {fpath}:{lineno} — [internal→modules·direct] {call_type}: '{target}'")
    for fpath in sh_files:  # Gate #8 v2 — Phase 2: invoke_module_interface validation
        if classify_layer(fpath) in _IMPORTING:
            violations = _validate_interfaces(_detect_invoke_calls(fpath), violations, fpath)
    for mf in sorted((CORE_DIR / "modules").rglob("Makefile")):  # Makefile contract
        if not any(inc in mf.read_text(encoding="utf-8", errors="replace") for inc in _MK_INCLUDES):
            violations.append(f"  {mf} — [modules·makefile-contract] missing module.mk include")
    return sorted(violations)
