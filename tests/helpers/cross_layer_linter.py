#!/usr/bin/env python3
"""Cross-layer import linter — implementation extracted from tests/test_cross_layer_imports.py (DevPlan 139 W3 T5).

# GREP_SUMMARY: cross-layer linter, static-analysis, layer-isolation, entrypoints, internal, modules, data-flow, extended-registry, shellcheck, variable-tracking, dotted-imports, python3-m, direction-allowlist
# STRUCTURE: ▶ discover(core/**/*.{sh,py,Makefile}) → ○ classify layer → ▶ scan imports (7 patterns + ShellCheck) → ▶ resolve (dotted→core/ path, extended registry + trace) → ◇ direction allowed? → ◇ direction-allowlist? → ⊕ collect violations → ⎋ lint_core() list[str]
"""
# region MODULE_CONTRACT
## @purpose  Реализация cross-layer линтера (была встроена в tests/test_cross_layer_imports.py,
##           1809 LOC). Извлечена в helper (DevPlan 139 W3 T5, S7): тестовый файл переписан
##           на direction-based allowlist, целевой объём ≤600 LOC.
## @scope    Сканер core/**/*.{sh,py,Makefile} для слоёв entrypoints/internal/modules:
##           - 7 паттернов импортов (source, ., exec, bash/sh, make -C, docker compose -f, python3 -m)
##           - Extended Variable Registry (auto-collect из paths.sh) + local trace
##           - ShellCheck data-flow (SC2154) с graceful degradation
##           - Gate #8 v2: direct module calls из internal/ + invoke_module_interface валидация
##           - Dotted-name импорты (core.internal.X) и python3 -m core.internal.* (U-09)
## @invariants
##   - Только файлы entrypoints/internal/modules подпадают под правила
##   - LINT-EXEMPT больше НЕ подавляет нарушения (TASK-6C) — warning
##   - Direction-allowlist (S7): разрешённые НАПРАВЛЕНИЯ (слои), не пары модулей и не (file, lineno)
##   - R5-negative фикстуры (core/modules/_b11_negative_*_tmp/) вне scope-префикса allowlist → RED
##   - НОВОЕ dotted-нарушение вне allowlist → RED (allowlist не растёт)
##   - Zero violations → [] ; любое нарушение → строки отчёта
## @rationale Извлечение линтера из тестового файла — единственный способ достичь ≤600 LOC
##            без потери детекции (сканер ~1100 LOC). tests/gates/test_gate_cross_layer.py
##            продолжает импортировать lint_core через test_cross_layer_imports (re-export).
## @changes  2026-08-05 | DevPlan 139 W3 T5 — извлечён из test_cross_layer_imports.py;
##           allowlist (file, lineno) → direction-based (source_layer, target_layer, path_prefix)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

from tests.helpers.gate_helpers import repo_root

logger = logging.getLogger(__name__)

# ─── CONSTANTS ──────────────────────────────────────────────────────────
CORE_DIR = repo_root() / "core"

# Layer classification: path prefixes to layer names
_LAYER_PREFIXES: dict[str, str] = {
    "entrypoints": "entrypoints/",
    "internal": "internal/",
    "modules": "modules/",
    "lib": "lib/",
    "templates": "templates/",
    "scripts": "scripts/",
    "bootstrap": "bootstrap/",
    "schemas": "schemas/",
}

# Import rules: source_layer -> set of allowed target layers
_IMPORT_RULES: dict[str, set[str]] = {
    "entrypoints": {"internal", "lib"},
    "internal": {"internal", "lib", "modules"},  # modules разрешён через typed contract (Gate #8 v2)
    "modules": {"lib", "templates"},
}

# Invoke contract: which interface names are valid for invoke_module_interface
_VALID_INTERFACES: set[str] = {"healthcheck", "install", "deploy-hook", "remove-hook"}

# Only these source layers are subject to import rules
_IMPORTING_LAYERS: set[str] = {"entrypoints", "internal", "modules"}

# Allowed Makefile includes from modules/ (exact relative paths)
_MODULE_MAKEFILE_ALLOWED_INCLUDES: set[str] = {
    "../../templates/module.mk",
    "../../templates/module-system.mk",
    "../../Makefile.common",
}

# Shell flags and commands that are NOT script imports
_NON_IMPORT_ARGS: set[str] = {
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
    # Special shell variables — not paths
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

# Patterns that indicate a non-path argument (bare variable, flag, etc.)
_RE_NOT_A_PATH = re.compile(r'^[\s\$"\'@*]+$')

# ─── DOTTED-NAME DETECTION (DevPlan 116 B11 T1, U-09) ───────────────────────
_RE_DOTTED_NAME = re.compile(r"^[a-z_][\w]*(\.[a-z_][\w]*)+$")

# ─── DIRECTION-BASED ALLOWLIST (DevPlan 139 W3 T5, S7) ─────────────────────
# Канон B8 D3 строгий режим (DevPlan 116 B11 T1), переведён с (file, lineno) на
# НАПРАВЛЕНИЯ: (source_layer, target_layer, path_prefix, reason). Преимущество:
# номера строк дрейфуют (исторические правки «строка 40 → 57», «+7 строк») —
# direction+scope стабильнее. R5-negative фикстуры (core/modules/_b11_negative_*_tmp/)
# НЕ попадают под prefix → модуль→internal остаётся RED для них.
# Модули контейнеризированы и импортируют internal/shared по дизайну (D1).
# allowlist НЕ растёт: ЛЮБОЕ новое нарушение вне scope → RED.
# Каждая запись имеет # LINT-EXEMPT: <reason> комментарий на строке нарушения.
_DIRECTION_ALLOWLIST: tuple[tuple[str, str, str, str], ...] = (
    (
        "modules",
        "internal",
        "core/modules/postgres/hooks/",
        "postgres-hook; shared.node_yaml — by design (D1); Python-канон D65. "
        "Контейнерный runtime, shared — единственный путь (shared/AGENTS.md инвариант 4). "
        "DevPlan 133 W2: роль/GRANT/credentials расширение (2026-08-03)",
    ),
)


def _repo_relative(source_file: Path) -> str:
    """Repo-root-relative posix path of a source file (allowlist key)."""
    try:
        return source_file.resolve().relative_to(repo_root().resolve()).as_posix()
    except ValueError:
        return source_file.as_posix()


def _is_direction_allowlisted(source_file: Path, source_layer: str, target_layer: str) -> bool:
    """Check (source_layer, target_layer, path_prefix) against the direction allowlist.

    ## @purpose — S7 direction-based: разрешённое направление + scope-префикс пути.
    ##            R5-negative фикстуры вне scope → RED (не подавляются).
    ## @io — ⇥ source_file, source_layer, target_layer → ⎋ bool
    ## @complexity — O(A) where A = allowlist entries
    """
    rel = _repo_relative(source_file)
    for src, tgt, prefix, reason in _DIRECTION_ALLOWLIST:
        if source_layer == src and target_layer == tgt and rel.startswith(prefix):
            logger.info(
                "[IMP:9][lint][allowlist] %s — direction %s→%s allowlisted (scope '%s'): %s",
                rel,
                src,
                tgt,
                prefix,
                reason,
            )
            return True
    return False


# ─── VARIABLE REGISTRY (DataFlow Extended Registry) ─────────────────────────


def _collect_path_variables(paths_file: Path | None = None) -> dict[str, str]:
    """Parse core/lib/paths.sh and extract all VAR=value assignments."""
    if paths_file is None:
        paths_file = CORE_DIR / "lib" / "paths.sh"

    if not paths_file.exists():
        logger.warning("[IMP:7][collect_vars] paths.sh not found at %s — returning empty dict", paths_file)
        return {}

    try:
        content = paths_file.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][collect_vars] Cannot read %s: %s", paths_file, exc)
        return {}

    variables: dict[str, str] = {}
    pattern = re.compile(r"^(?:readonly\s+|export\s+)?(\w+)=(.*)")

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        m = pattern.match(stripped)
        if not m:
            continue

        var_name = m.group(1)
        raw_value = m.group(2).strip()

        if (raw_value.startswith('"') and raw_value.endswith('"')) or (
            raw_value.startswith("'") and raw_value.endswith("'")
        ):
            raw_value = raw_value[1:-1]

        comment_pos = raw_value.find(" #")
        if comment_pos > 0:
            raw_value = raw_value[:comment_pos].rstrip()

        if "${BASH_SOURCE[0]}" in raw_value or "$(cd" in raw_value or "$(dirname" in raw_value:
            resolved = _resolve_bash_source_var(var_name, paths_file)
            if resolved:
                variables[var_name] = resolved
            continue

        variables[var_name] = raw_value

    for name in list(variables.keys()):
        value = variables[name]
        if "${" in value:
            for nested_name, nested_value in variables.items():
                value = value.replace(f"${{{nested_name}}}", nested_value)
                value = value.replace(f"${nested_name}", nested_value)
            variables[name] = value

    logger.info("[IMP:8][collect_vars] Collected %d variables from %s", len(variables), paths_file)
    return variables


def _resolve_bash_source_var(var_name: str, paths_file: Path) -> str | None:
    """Resolve variables that depend on ${BASH_SOURCE[0]} statically."""
    lib_dir = paths_file.parent.resolve()
    core_dir = lib_dir.parent  # core/

    static_map: dict[str, str] = {
        "PATHS_LIB_DIR": str(lib_dir),
        "PATHS_CORE_DIR": str(core_dir),
        "PATHS_MODULES_DIR": str(core_dir / "modules"),
        "PATHS_TEMPLATES_DIR": str(core_dir / "templates"),
        "PATHS_INTERNAL_DIR": str(core_dir / "internal"),
    }
    return static_map.get(var_name)


# Module-level: auto-collected at import time from paths.sh
_KNOWN_PATH_VARIABLES: dict[str, str] = _collect_path_variables()


# ─── LAYER CLASSIFICATION ───────────────────────────────────────────────


def classify_layer(file_path: Path) -> str | None:
    """Determine which layer a file belongs to based on its path relative to core/."""
    path_str = file_path.as_posix()
    for layer, prefix in _LAYER_PREFIXES.items():
        if f"core/{prefix}" in path_str:
            return layer
    return None


def _resolve_platform_root(source_file: Path, source_layer: str) -> Path:
    """Infer the PLATFORM_ROOT variable value based on source file location."""
    if source_layer == "entrypoints":
        return CORE_DIR
    if source_layer == "internal":
        path_str = source_file.as_posix()
        if "/internal/scaffold/" in path_str or "/internal/build/" in path_str:
            return repo_root()
        return CORE_DIR
    return CORE_DIR


def _looks_like_path(text: str) -> bool:
    """Check if an import argument looks like a file path (not a bare variable or flag)."""
    t = text.strip().strip("'\"")
    if not t:
        return False

    has_separator = "/" in t
    has_var_prefix = t.startswith("${") and "/" in t
    has_relative = t.startswith("..")
    has_absolute = t.startswith("/") and t != "/"

    is_bare_variable = (
        t.startswith("$")
        and len(t) > 1
        and not t.startswith("${")
        and t not in _NON_IMPORT_ARGS
        and not re.match(r"^\$[\d@*!#?\-]$", t)
    )

    is_dotted_name = bool(_RE_DOTTED_NAME.match(t)) and not t.startswith("$")

    return has_separator or has_var_prefix or has_relative or has_absolute or is_bare_variable or is_dotted_name


def _substitute_variables(resolved: str, source_file: Path, source_layer: str) -> str:
    """Substitute known variables in an import path (auto-collected + contextual)."""
    for var_name, var_value in _KNOWN_PATH_VARIABLES.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    contextual = {
        "_EP_DIR": str(source_file.parent),
        "SCRIPT_DIR": str(source_file.parent),
        "MODULE_DIR": str(source_file.parent),
        "_HEALTHCHECK_LIB_DIR": str(CORE_DIR / "lib"),
        "_TIMING_LIB_DIR": str(CORE_DIR / "lib"),
        "_NODE_RESOLVER_LIB_DIR": str(CORE_DIR / "lib"),
        "CORE_DIR": str(CORE_DIR),
        "PATHS_INTERNAL_DIR": str(CORE_DIR / "internal"),
        "PLATFORM_ROOT": str(_resolve_platform_root(source_file, source_layer)),
    }
    for var_name, var_value in contextual.items():
        resolved = resolved.replace(f"${{{var_name}}}", var_value)
        resolved = resolved.replace(f"${var_name}", var_value)

    return resolved


def _trace_variable_assignment(file_path: Path, var_name: str) -> str | None:
    """Trace a variable to its last assignment in the same file."""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None

    pattern = rf'(?:local\s+|export\s+|readonly\s+)?{re.escape(var_name)}=["\']?([^"\'\n]+)'

    matches = list(re.finditer(pattern, content))
    if not matches:
        return None

    last_match = matches[-1]
    value = last_match.group(1).strip()

    for nested_name, nested_value in _KNOWN_PATH_VARIABLES.items():
        value = value.replace(f"${{{nested_name}}}", nested_value)
        value = value.replace(f"${nested_name}", nested_value)

    if "/" in value:
        return value
    return None


def resolve_import(source_file: Path, import_path: str, source_layer: str) -> Path | None:
    """Resolve an import path to an absolute target path (None if not resolvable)."""
    if not _looks_like_path(import_path):
        return None

    resolved = import_path.strip()
    resolved = _substitute_variables(resolved, source_file, source_layer)

    # Dotted-name → core/ path (DevPlan 116 B11 T1)
    if "/" not in resolved and _RE_DOTTED_NAME.match(resolved):
        if resolved.startswith("core.internal."):
            rel = resolved[len("core.internal.") :].replace(".", "/")
            resolved = f"{CORE_DIR}/internal/{rel}"
        else:
            resolved = resolved.replace(".", "/")

    if resolved.startswith("$") and "/" not in resolved:
        var_name = resolved.lstrip("$").strip("{}")
        traced = _trace_variable_assignment(source_file, var_name)
        if traced:
            resolved = traced

    resolved = resolved.replace('"', "").replace("'", "")

    if resolved.startswith("./"):
        resolved = resolved[2:]

    if "/" not in resolved:
        return None

    if not resolved.startswith("/") and not resolved.startswith("..") and not resolved.startswith("${"):
        return None

    result = Path(resolved)
    if result.is_absolute():
        final = result.resolve()
    else:
        final = (source_file.parent / result).resolve()

    if not str(final).startswith(str(CORE_DIR)):
        return None

    return final


def layer_of_target(resolved_path: Path) -> str | None:
    """Determine which layer a resolved target path belongs to."""
    path_str = resolved_path.as_posix()
    for layer, prefix in _LAYER_PREFIXES.items():
        if f"core/{prefix}" in path_str:
            return layer
    return None


# ─── SCANNERS ───────────────────────────────────────────────────────────


def _has_lint_exempt(lines: list[str], lineno: int) -> bool:
    """Check if the given or preceding line has a LINT-EXEMPT comment."""
    for check in (lineno - 1, lineno - 2):
        if 0 <= check < len(lines):
            line = lines[check].strip()
            if "# LINT-EXEMPT:" in line:
                return True
    return False


def scan_sh_file(file_path: Path, source_layer: str | None = None) -> list[tuple[int, str, bool]]:
    """Scan a .sh file for sourcing and direct script invocations.

    Returns list of (lineno, import_path, has_exempt). For modules/, /opt/ paths
    are NOT filtered (they may be cross-layer violations).
    """
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][sh] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")
    skip_opt_filter = source_layer == "modules"

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        m = re.search(r"(?:^|\s)(?:source)\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        m = re.search(r"(?:^|\s)\.\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in ('"$@"', "${@}", "$@", '".",'):
                continue
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        m = re.search(r"(?:^|;|&&|\|\|)\s*exec\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in (">", ">>", "<", "2>", "2>>", ";"):
                continue
            if path.startswith((">", "<")):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        m = re.search(r"(?:^|\s)(?:bash|/bin/bash|sh|/bin/sh)\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if path in _NON_IMPORT_ARGS:
                continue
            if path.startswith("-"):
                continue
            if not skip_opt_filter and (path.startswith(("/etc/", "/opt/"))):
                continue
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        m = re.search(r"(?:^|\s)make\s+-C\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if _looks_like_path(path):
                imports.append((i, path, exempt))
            continue

        m = re.search(r"(?:^|\s)docker[\s-]+compose\s+(?:.*\s)?-f\s+(\S+)", stripped)
        if m:
            path = m.group(1)
            if _looks_like_path(path) and path not in ("-f",):
                imports.append((i, path, exempt))
            continue

        # Pattern 7: python3 -m <module> (DevPlan 116 B11 T1, U-09)
        m = re.search(r"python3\s+-m\s+(\S+)", stripped)
        if m:
            mod = m.group(1).rstrip("\\")
            if _looks_like_path(mod):
                imports.append((i, mod, exempt))
            continue

    # ShellCheck data-flow analysis (дополнительный слой, graceful degradation)
    if source_layer in _IMPORTING_LAYERS:
        try:
            from _conftest.shellcheck import get_shellcheck_bash_calls

            shellcheck_calls = get_shellcheck_bash_calls(file_path)
            for shell_lineno, imp_path in shellcheck_calls:
                already_found = any(
                    existing_lineno == shell_lineno and existing_path == imp_path
                    for existing_lineno, existing_path, _ in imports
                )
                if not already_found:
                    exempt = _has_lint_exempt(lines, shell_lineno)
                    imports.append((shell_lineno, imp_path, exempt))
        except ImportError:
            logger.debug("[IMP:5][scan][shellcheck] Module not available for %s", file_path)
        except Exception as exc:
            logger.warning("[IMP:6][scan][shellcheck] Error for %s: %s", file_path, exc)

    return imports


def scan_py_file(file_path: Path) -> list[tuple[int, str, bool]]:
    """Scan a .py file for import statements."""
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][py] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        m = re.search(r"^from\s+(\S+)\s+import", stripped)
        if m:
            imports.append((i, m.group(1), exempt))
            continue

        m = re.search(r"^import\s+(\S+)", stripped)
        if m:
            mod = m.group(1)
            mod = mod.split(",")[0] if "," in mod else mod
            imports.append((i, mod, exempt))
            continue

    return imports


def scan_makefile(file_path: Path) -> list[tuple[int, str, bool]]:
    """Scan a Makefile for include statements."""
    imports: list[tuple[int, str, bool]] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("[IMP:6][scan][make] Cannot read %s: %s", file_path, exc)
        return imports

    lines = content.split("\n")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        exempt = _has_lint_exempt(lines, i)

        m = re.search(r"^include\s+(\S+)", stripped)
        if m:
            imports.append((i, m.group(1), exempt))
            continue

    return imports


# ─── GATE #8 V2: INVOKE DETECTION ─────────────────────────────────────────


def _detect_invoke_calls(source_file: Path) -> list[dict]:
    """Find all `invoke_module_interface <module> <interface>` calls in a file."""
    calls: list[dict] = []
    try:
        content = source_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return calls

    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.search(
            r"invoke_module_interface\s+"
            r'"?([a-zA-Z0-9_-]+)"?\s+'
            r'"?"?([a-zA-Z0-9_-]+)"?"?',
            stripped,
        )
        if m:
            module = m.group(1)
            interface = m.group(2)
            if module.startswith("$") or interface.startswith("$"):
                calls.append({"module": module, "interface": interface, "lineno": i, "warn": True})
            else:
                calls.append({"module": module, "interface": interface, "lineno": i, "warn": False})

    return calls


def _validate_interfaces(invoke_calls: list[dict], violations: list[str], source_file: Path) -> list[str]:
    """For each invoke call, verify the interface is registered in module.yaml.interfaces."""
    for call in invoke_calls:
        lineno = call["lineno"]
        module = call["module"]
        interface = call["interface"]

        if call.get("warn"):
            logger.warning(
                "[IMP:7][gate8-v2][warn] %s:%d — invoke_module_interface with variable args — cannot statically validate",
                source_file,
                lineno,
            )
            continue

        if interface not in _VALID_INTERFACES:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] "
                f"Unknown interface '{interface}' for module '{module}' — "
                f"valid interfaces: {sorted(_VALID_INTERFACES)}"
            )
            continue

        module_yaml = CORE_DIR / "modules" / module / "module.yaml"
        if not module_yaml.exists():
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] module.yaml not found for module '{module}'"
            )
            continue

        try:
            content = module_yaml.read_text(encoding="utf-8", errors="replace")
        except Exception:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] Cannot read module.yaml for '{module}'"
            )
            continue

        registered_interfaces: list[str] = []
        in_interfaces = False
        for yaml_line in content.split("\n"):
            stripped_line = yaml_line.strip()
            if stripped_line == "interfaces:":
                in_interfaces = True
                continue
            if in_interfaces:
                if stripped_line and not stripped_line.startswith("-") and ":" in stripped_line:
                    in_interfaces = False
                    continue
                list_match = re.match(r"^\s*-\s+(\S+)", stripped_line)
                if list_match:
                    registered_interfaces.append(list_match.group(1))
                elif not stripped_line or stripped_line.startswith("#"):
                    continue
                else:
                    if stripped_line == "[]":
                        in_interfaces = False
                        continue
                    in_interfaces = False

        if interface not in registered_interfaces:
            violations.append(
                f"  {source_file}:{lineno} — [internal→modules·invoke] "
                f"Interface '{interface}' NOT REGISTERED for module '{module}' — "
                f"registered: {registered_interfaces if registered_interfaces else '[]'}"
            )

    return violations


def _detect_direct_module_calls(source_file: Path) -> list[tuple[int, str, str]]:
    """Detect direct bash/source/. calls to modules/ from internal/ without invoke_module_interface."""
    direct_calls: list[tuple[int, str, str]] = []
    try:
        content = source_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return direct_calls

    for i, line in enumerate(content.split("\n"), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "invoke_module_interface" in stripped:
            continue

        for cmd in ["bash", "sh", "/bin/bash", "/bin/sh"]:
            m = re.search(rf"(?:^|\s)(?:{cmd})\s+(\S+)", stripped)
            if m:
                target = m.group(1)
                if "modules/" in target:
                    direct_calls.append((i, "bash (direct path)", target))
                elif target.startswith(("$", '"$')) and _resolve_var_to_modules_path(target, content):
                    direct_calls.append((i, "bash (variable → modules/)", target))

        m = re.search(r"(?:^|\s)(?:source)\s+(\S+)", stripped)
        if m:
            target = m.group(1)
            if "modules/" in target:
                direct_calls.append((i, "source (direct path)", target))

        m = re.search(r"(?:^|\s)\.\s+(\S+)", stripped)
        if m:
            target = m.group(1)
            if target not in ('"$@"', "${@}", "$@", '".",') and "modules/" in target:
                direct_calls.append((i, ". (direct path)", target))

    return direct_calls


def _resolve_var_to_modules_path(var_ref: str, file_content: str) -> bool:
    """Check if a variable reference was assigned from a modules/ path."""
    var_name = var_ref.strip().strip('"').lstrip("${").rstrip("}")
    var_name = var_name.lstrip("$")

    if not var_name:
        return False

    patterns = [
        rf"local\s+{re.escape(var_name)}\s*=\s*.*modules/",
        rf"{re.escape(var_name)}\s*=\s*.*modules/",
        rf"local\s+{re.escape(var_name)}\s*=\s*.*dirname\s+\${{?module_yaml}}?.*hook",
    ]
    return any(re.search(pattern, file_content) for pattern in patterns)


# ─── VIOLATION CHECK ─────────────────────────────────────────────────────


def check_violation(
    source_file: Path,
    lineno: int,
    import_path: str,
    import_type: str,
    exempt: bool,
    resolved: Path | None = None,
) -> str | None:
    """Check if an import violates cross-layer rules.

    Returns formatted violation string, or None if allowed.
    LINT-EXEMPT no longer suppresses violations (TASK-6C); a warning is logged.
    """
    if exempt:
        logger.warning(
            "[IMP:7][lint][LINT-EXEMPT] %s:%d — LINT-EXEMPT present but no longer suppresses violations (TASK-6C).",
            source_file,
            lineno,
        )

    source_layer = classify_layer(source_file)
    if source_layer is None or source_layer not in _IMPORTING_LAYERS:
        return None

    if import_type == "make":
        if source_layer == "modules":
            if import_path in _MODULE_MAKEFILE_ALLOWED_INCLUDES:
                return None
            return (
                f"  {source_file}:{lineno} — [modules·make] "
                f"include '{import_path}' — only ../../templates/module.mk, "
                f"../../templates/module-system.mk, or ../../Makefile.common allowed"
            )
        return None

    if resolved is None:
        return None
    target_layer = layer_of_target(resolved)
    if target_layer is None:
        return None
    allowed = _IMPORT_RULES.get(source_layer, set())
    if target_layer in allowed:
        return None
    # Direction-based allowlist (S7): направление + scope-префикс, не (file, lineno)
    if _is_direction_allowlisted(source_file, source_layer, target_layer):
        return None
    return f"  {source_file}:{lineno} — [{source_layer}→{target_layer}] '{import_path}' (forbidden)"


# ─── MAIN LINT LOGIC ─────────────────────────────────────────────────────


def lint_core() -> list[str]:
    """Run the cross-layer import linter across all files in core/.

    Gate #8 v2 phases: direct module calls from internal/ + invoke validation.
    Returns sorted list of violation strings.
    """
    violations: list[str] = []

    sh_files = sorted(CORE_DIR.rglob("*.sh"))
    py_files = sorted(CORE_DIR.rglob("*.py"))
    make_files = sorted(CORE_DIR.rglob("Makefile"))

    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_sh_file(fpath, source_layer)
        for lineno, imp_path, exempt in imports:
            resolved = resolve_import(fpath, imp_path, source_layer)
            msg = check_violation(fpath, lineno, imp_path, "sh", exempt, resolved)
            if msg:
                violations.append(msg)

    for fpath in py_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_py_file(fpath)
        for lineno, imp_path, exempt in imports:
            resolved = resolve_import(fpath, imp_path, source_layer)
            msg = check_violation(fpath, lineno, imp_path, "py", exempt, resolved)
            if msg:
                violations.append(msg)

    for fpath in make_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        imports = scan_makefile(fpath)
        for lineno, imp_path, exempt in imports:
            msg = check_violation(fpath, lineno, imp_path, "make", exempt)
            if msg:
                violations.append(msg)

    # Gate #8 v2 — Phase 1: direct module calls from internal/
    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer != "internal":
            continue
        direct_calls = _detect_direct_module_calls(fpath)
        for lineno, call_type, target in direct_calls:
            violations.append(
                f"  {fpath}:{lineno} — [internal→modules·direct] "
                f"Direct module call ({call_type}): '{target}' — use invoke_module_interface instead"
            )

    # Gate #8 v2 — Phase 2: invoke_module_interface validation
    for fpath in sh_files:
        source_layer = classify_layer(fpath)
        if source_layer not in _IMPORTING_LAYERS:
            continue
        invoke_calls = _detect_invoke_calls(fpath)
        if invoke_calls:
            violations = _validate_interfaces(invoke_calls, violations, fpath)

    # Makefile Contract Check: every modules/*/Makefile must include module.mk
    modules_makefiles = sorted((CORE_DIR / "modules").rglob("Makefile"))
    read_errors: list[Path] = []
    for mf in modules_makefiles:
        try:
            content = mf.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logger.error("[IMP:7][lint_core] Failed to read Makefile: %s", mf, exc_info=True)
            read_errors.append(mf)
            continue
        has_allowed_include = any(allowed in content for allowed in _MODULE_MAKEFILE_ALLOWED_INCLUDES)
        if not has_allowed_include:
            violations.append(
                f"  {mf} — [modules·makefile-contract] "
                f"Missing include of {_MODULE_MAKEFILE_ALLOWED_INCLUDES} — "
                f"every module Makefile must include templates/module.mk or module-system.mk"
            )

    if read_errors:
        raise RuntimeError(f"Failed to read {len(read_errors)} Makefile(s): {read_errors}")

    return sorted(violations)
