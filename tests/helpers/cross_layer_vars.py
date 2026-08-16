#!/usr/bin/env python3
"""Path/variable resolver for cross-layer linter — extracted registry (DevPlan 163 W-D D2).

# GREP_SUMMARY: cross-layer vars, variable-registry, path-resolution, paths.sh, dotted-name, resolve-import, trace-variable, looks-like-path
# STRUCTURE: ▶ paths.sh registry (_STATIC_VARS + _collect_path_variables) → ▶ _looks_like_path → ▶ _substitute_vars + _trace_variable_assignment → ▶ resolve_import (dotted→core/ path) → ⎋ Path|None
"""
# region MODULE_CONTRACT
## @purpose  Вынесенный путевой резолвер shell-импортов (variable registry + dotted-name резолв).
##           Отдельный модуль позволяет cross_layer_linter.py остаться <200 LOC (DoD W-D).
## @scope    paths.sh переменные (5 readonly PATHS_* + PLATFORM_ROOT), контекстные переменные,
##           локальный trace присвоений, dotted-name → core/ путь (python3 -m core.internal.* U-09).
## @invariants
##   - resolve_import возвращает Path только внутри CORE_DIR (иначе None)
##   - ${...} без "/" — НЕ путь (bare-переменная); dotted-name резолвится только без "$"
##   - LINT-EXEMPT/trace/registry семантика сохранена из старого линтера (parity, §4.3)
## @rationale Extended Variable Registry (DevPlan 139 W3) — shell-инфраструктура; вынос
##            сохраняет API (re-export в cross_layer_linter.py) и ужимает основной линтер.
## @changes  2026-08-13 | DevPlan 163 W-D D2 — извлечён из tests/helpers/cross_layer_linter.py (881 split)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

CORE_DIR = repo_root() / "core"
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
_DOTTED = re.compile(r"^[a-z_][\w]*(\.[a-z_][\w]*)+$")
_STATIC_VARS = {
    "PATHS_LIB_DIR": str(CORE_DIR / "lib"),
    "PATHS_CORE_DIR": str(CORE_DIR),
    "PATHS_MODULES_DIR": str(CORE_DIR / "modules"),
    "PATHS_TEMPLATES_DIR": str(CORE_DIR / "templates"),
    "PATHS_INTERNAL_DIR": str(CORE_DIR / "internal"),
    "PLATFORM_ROOT": "/opt/platform",
}


def _collect_path_variables(paths_file: Path | None = None) -> dict[str, str]:
    """Parse core/lib/paths.sh VAR=value assignments (Extended Registry, compat API)."""
    paths_file = paths_file or CORE_DIR / "lib" / "paths.sh"
    variables: dict[str, str] = {}
    if not paths_file.exists():
        logger.warning("[IMP:7][collect_vars] paths.sh not found at %s", paths_file)
        return variables
    try:
        lines = paths_file.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError as exc:
        logger.warning("[IMP:6][collect_vars] Cannot read %s: %s", paths_file, exc)
        return variables
    for line in lines:
        m = re.match(r"^(?:readonly\s+|export\s+)?(\w+)=(.*)", line.strip())
        if m and not line.strip().startswith("#"):
            raw = m.group(2).strip().strip('"').split(" #")[0].rstrip()
            if "${BASH_SOURCE[0]}" in raw:
                if m.group(1) == "PATHS_LIB_DIR":
                    variables[m.group(1)] = str(paths_file.parent)
                continue
            variables[m.group(1)] = raw
    for name, value in list(variables.items()):
        for n2, v2 in variables.items():
            variables[name] = value.replace(f"${{{n2}}}", v2).replace(f"${n2}", v2)
    logger.info("[IMP:8][collect_vars] Collected %d variables from %s", len(variables), paths_file)
    return variables


_KNOWN_VARS: dict[str, str] = {**_STATIC_VARS, **_collect_path_variables()}


def _looks_like_path(text: str) -> bool:
    """True if an import argument looks like a path or dotted-name (not bare var/flag)."""
    t = text.strip().strip("'\"")
    bare_var = (
        t.startswith("$")
        and len(t) > 1
        and not t.startswith("${")
        and t not in _NO_PATH
        and not re.match(r"^\$[\d@*!#?\-]$", t)
    )
    # ${...} считается путём только с "/" (как в старом линтере) — bare `${internal}` не импорт
    return bool(t) and (
        "/" in t
        or t.startswith("..")
        or (t.startswith("${") and "/" in t)
        or bare_var
        or (bool(_DOTTED.match(t)) and not t.startswith("$"))
    )


def _substitute_vars(resolved: str, source_file: Path) -> str:
    """Substitute known path variables (registry + contextual)."""
    # ⚠️ TRAP[BUG] · 2026-08-13 · P2 · Вложенные переменные paths.sh (DevPlan 163 W-G)
    # · Symptom: resolve_import("${PATHS_MODULES_DIR}/postgres/healthcheck.sh") → None —
    # ·   test_known_variable_substitution/test_nested_variable_substitution падали
    # · Root: paths.sh определяет PATHS_MODULES_DIR = "${PATHS_CORE_DIR}/modules" (вложенная
    # ·   ссылка); _collect_path_variables() перекрывает _STATIC_VARS; ОДИН проход replace
    # ·   оставляет ${PATHS_CORE_DIR} литералом → final вне CORE_DIR → None (слепота импорта)
    # · Fix: итеративная подстановка до фикс-точки (≤4 прохода — глубина paths.sh)
    # · Prevention: unit-тесты TestResolveImport покрывают вложенные ссылки
    ctx = {
        "_EP_DIR": str(source_file.parent),
        "SCRIPT_DIR": str(source_file.parent),
        "MODULE_DIR": str(source_file.parent),
        "CORE_DIR": str(CORE_DIR),
        "PATHS_INTERNAL_DIR": str(CORE_DIR / "internal"),
        "PLATFORM_ROOT": str(
            repo_root()
            if "/internal/scaffold/" in source_file.as_posix() or "/internal/build/" in source_file.as_posix()
            else CORE_DIR
        ),
    }
    table = {**_KNOWN_VARS, **ctx}
    for _ in range(4):  # фикс-точка: nested refs paths.sh (макс глубина 2-3)
        prev = resolved
        for var_name, var_value in table.items():
            resolved = resolved.replace(f"${{{var_name}}}", var_value).replace(f"${var_name}", var_value)
        if resolved == prev:
            break
    return resolved


def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    """Trace a variable to its last assignment in the same file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.findall(rf'(?:local\s+|export\s+|readonly\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)', content)
    if not m:
        return None
    value = m[-1].strip()
    for n, v in _KNOWN_VARS.items():
        value = value.replace(f"${{{n}}}", v).replace(f"${n}", v)
    return value if "/" in value else None


def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    """Resolve an import path to an absolute core/ target (None if not resolvable)."""
    if not _looks_like_path(import_path):
        return None
    resolved = _substitute_vars(import_path.strip(), source_file)
    if "/" not in resolved and _DOTTED.match(resolved):  # python3 -m core.internal.* (U-09)
        if resolved.startswith("core.internal."):
            resolved = f"{CORE_DIR}/internal/{resolved[len('core.internal.') :].replace('.', '/')}"
        else:
            resolved = resolved.replace(".", "/")
    if resolved.startswith("$") and "/" not in resolved:
        traced = _trace_variable_assignment(source_file, resolved.lstrip("$").strip("{}"))
        if traced:
            resolved = traced
    resolved = resolved.replace('"', "").replace("'", "")
    if resolved.startswith("./"):
        resolved = resolved[2:]
    if "/" not in resolved or not resolved.startswith(("/", "..", "${")):
        return None
    final = Path(resolved).resolve() if Path(resolved).is_absolute() else (source_file.parent / resolved).resolve()
    return final if str(final).startswith(str(CORE_DIR)) else None
