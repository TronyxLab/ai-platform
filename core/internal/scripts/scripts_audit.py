#!/usr/bin/env python3
# GREP_SUMMARY: scripts-audit, shebang-registration, pre-commit, gate-exceptions, manifest, yaml-parser
# STRUCTURE: ▶ walk core/**/*.sh → ◇ shebang check (head line #!) → ◇ exception fnmatch → ◇ yaml-registration substring → ⊕ report unregistered → ⎋ exit 0|1
# region MODULE_CONTRACT
## @purpose  Audit: every shebang file under core/ must be registered in
##           entrypoint-manifest.yaml (delegates_to or module_hooks) OR match an
##           exception pattern. Exit 0 = all registered, 1 = violations.
##           Python-порт scripts-audit.sh (DevPlan 118 E6): yaml-парсер entrypoint-manifest
##           вместо grep (DRIFT-подобная устойчивость к комментариям/описаниям), fnmatch
##           вместо bash glob (тот же semantics для * across /), отчёт в том же формате.
## @scope    All .sh files with shebang under core/ (excluding __pycache__, .backup, node_modules).
##           Вызывается из make scripts-audit через тонкий фасад core/internal/scripts-audit.sh.
## @io       Reads core/entrypoint-manifest.yaml → exit 0 (clean) | exit 1 + list of unregistered scripts
## @invariants
##   - Reads only first line of each .sh for shebang detection (#! prefix)
##   - Exception patterns use fnmatch (bash glob semantics: * spans /) against relative
##     path from project root — идентично старому `[[ "$rel" == $pattern ]]`
##   - Manifest check: deep-walk YAML string values, substring-match rel path (аналог
##     старого grep -qF — false positives возможны, но acceptable, комментарии/описания)
##   - Сам фасад core/internal/scripts-audit.sh остаётся в EXCEPTIONS (self-exception)
## @rationale Prevents registration drift — gate tests catch missing registrations
##            post-factum on CI; this hook catches them pre-commit. Strangler E6:
##            grep → yaml-парсер делает проверку устойчивой к реформаттингу manifest.
## @changes  2026-08-02 | DevPlan 118 E6 — Created (Python-порт scripts-audit.sh)
## @see      core/internal/scripts-audit.sh (тонкий фасад <10 LOC)
# endregion MODULE_CONTRACT

from __future__ import annotations

import fnmatch
import logging
import sys
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)

# ── Exception patterns (bash glob semantics via fnmatch) ───────────────────
# Scripts that legitimately don't need manifest registration.
# Patterns matched against relative path from project root.
EXCEPTIONS: tuple[str, ...] = (
    "core/lib/*",  # Libraries (sourced, not executed)
    "core/modules/*/healthcheck.sh",  # Module healthchecks
    "core/modules/*/hooks/*.sh",  # Module hooks
    "core/modules/*/install.sh",  # Module installers
    "core/modules/*/scripts/*.sh",  # Module scripts
    "core/modules/*/config/*.sh",  # Module configs
    "core/modules/*/config/*/*.sh",  # Nested module configs
    "core/internal/healthcheck/*.sh",  # Internal healthchecks
    "core/modules/hermes-agent/build/scripts/*",  # Hermes build
    "core/modules/hermes-agent/context/scripts/*",  # Hermes context
    "core/modules/nginx/nginx_reload_hook.sh",  # Nginx hook
    # reconcile-projects.sh — канал консолидирован в converge.sh
    "core/internal/hooks/*.sh",  # Pre-commit hooks (DevPlan 028 W1-E7)
    "core/internal/scripts-audit.sh",  # Self (тонкий фасад)
)

_SHEBANG_PREFIX = "#!"
_EXCLUDED_DIRS = (".backup", "__pycache__", "node_modules")


# region FUNC_collect_shebang_scripts
## @purpose  Найти все .sh файлы под core_dir (исключая кэш/бэкап/модули) с shebang-первой строкой.
## @io       ⇥ core_dir: Path → ⎋ list[Path] — файлы с shebang
## @complexity O(N) — walk по дереву + чтение первой строки
def collect_shebang_scripts(core_dir: Path) -> list[Path]:
    """Collect all .sh files under core_dir that have a shebang first line."""
    result: list[Path] = []
    for path in sorted(core_dir.rglob("*.sh")):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        try:
            first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
        except (OSError, IndexError):
            continue
        if first_line.startswith(_SHEBANG_PREFIX):
            result.append(path)
    logger.info("[IMP:8][scripts_audit][collect] Found %d shebang scripts under %s", len(result), core_dir)
    return result


# endregion FUNC_collect_shebang_scripts


# region FUNC_is_exception
## @purpose  Проверить rel-путь против EXCEPTIONS-паттернов (fnmatch, bash-glob семантика).
## @io       ⇥ rel: str → ⎋ bool
## @complexity O(E) — E = число паттернов
def is_exception(rel: str) -> bool:
    """Return True if rel path matches any exception pattern (fnmatch, * spans /)."""
    for pattern in EXCEPTIONS:
        if fnmatch.fnmatch(rel, pattern):
            logger.info("[IMP:8][scripts_audit][exception] %s matches %s", rel, pattern)
            return True
    return False


# endregion FUNC_is_exception


# region FUNC_collect_manifest_strings
## @purpose  Собрать все строковые значения из entrypoint-manifest.yaml (deep-walk) —
##           аналог grep -qF по файлу, но структурный (устойчив к реформаттингу).
## @io       ⇥ manifest_path: Path → ⎋ list[str] — все строки YAML-документа
## @complexity O(N) — N = размер YAML
def collect_manifest_strings(manifest_path: Path) -> list[str]:
    """Deep-walk YAML manifest and collect all string scalar values (substring-match corpus)."""
    if not manifest_path.is_file():
        logger.warning("[IMP:7][scripts_audit][manifest] Manifest not found at %s", manifest_path)
        return []
    try:
        # W11: yaml.safe_load returns Any → cast to object for deep-walk
        data: object = cast(object, yaml.safe_load(manifest_path.read_text(encoding="utf-8")))
    except yaml.YAMLError as exc:
        logger.error("[IMP:10][scripts_audit][manifest] YAML parse error in %s: %s", manifest_path, exc)
        return []

    strings: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            # W11: isinstance-narrowed dict is dict[Unknown, Unknown] → cast for values iteration
            for value in cast(dict[str, object], node).values():
                walk(value)
        elif isinstance(node, list):
            for item in cast(list[object], node):
                walk(item)
        elif isinstance(node, str):
            strings.append(node)

    walk(data)
    logger.info("[IMP:8][scripts_audit][manifest] Collected %d string values from manifest", len(strings))
    return strings


# endregion FUNC_collect_manifest_strings


# region FUNC_is_registered
## @purpose  Проверить регистрацию rel-пути в манифесте (substring-match против строк YAML).
## @io       ⇥ rel: str, manifest_strings: list[str] → ⎋ bool
## @complexity O(M * L) — M = строк, L = длина rel
def is_registered(rel: str, manifest_strings: list[str]) -> bool:
    """Return True if rel path appears (substring) in any manifest string value."""
    return any(rel in s for s in manifest_strings)


# endregion FUNC_is_registered


# region FUNC_audit
## @purpose  Полный аудит: собрать unregistered shebang-скрипты (не exception, не в manifest).
## @io       ⇥ core_dir: Path, project_root: Path, manifest_path: Path → ⎋ list[str] — rel-пути нарушителей
## @complexity O(N * (E + M*L))
def audit(core_dir: Path, project_root: Path, manifest_path: Path) -> list[str]:
    """Run the audit. Returns list of unregistered rel paths (empty = clean)."""
    manifest_strings = collect_manifest_strings(manifest_path)
    unregistered: list[str] = []
    for script in collect_shebang_scripts(core_dir):
        rel = str(script.relative_to(project_root))
        if is_exception(rel):
            continue
        if is_registered(rel, manifest_strings):
            continue
        unregistered.append(rel)
    return unregistered


# endregion FUNC_audit


# region FUNC_main
def main() -> int:
    """CLI entrypoint: `python3 -m core.internal.scripts.scripts_audit`.

    ▶ ┌argv┐ → ○ resolve core_dir/project_root/manifest → ○ audit() → ◇ unregistered? → ⎋ exit 1 | exit 0
    """
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    core_dir = Path(__file__).resolve().parents[2]  # core/
    project_root = core_dir.parent  # repo root
    manifest_path = core_dir / "entrypoint-manifest.yaml"

    unregistered = audit(core_dir, project_root, manifest_path)
    if unregistered:
        print("[IMP:10][scripts-audit] UNREGISTERED SCRIPTS FOUND:")
        for f in unregistered:
            print(f"  - {f}")
        print("")
        print("Action required:")
        print("  1. Register in core/entrypoint-manifest.yaml (delegates_to or module_hooks)")
        print("  2. OR add to EXCEPTIONS tuple in core/internal/scripts/scripts_audit.py")
        print("  3. Retry commit")
        return 1

    print("[IMP:9][scripts-audit] All shebang scripts registered or in exceptions")
    return 0


# endregion FUNC_main

if __name__ == "__main__":
    sys.exit(main())
