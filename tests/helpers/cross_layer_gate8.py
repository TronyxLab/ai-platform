"""Gate #8 v2 — typed contract (direct module calls + invoke validation) for cross-layer linter.

# GREP_SUMMARY: gate8, typed-contract, invoke-module-interface, direct-module-calls, module-yaml, interfaces, cross-layer, shell
# STRUCTURE: ▶ _detect_direct_module_calls (bash/source/. → modules/) → ▶ _resolve_var_to_modules_path → ▶ _detect_invoke_calls → ▶ _validate_interfaces (module.yaml#interfaces) → ⎋ violations
"""
# region MODULE_CONTRACT
## @purpose  Gate #8 v2: typed contract internal→modules (core/AGENTS.md) — direct shell-вызовы
##           modules/ из internal/ без invoke_module_interface = violation; invoke с
##           незарегистрированным интерфейсом (module.yaml#interfaces) = violation.
## @scope    Вынесен из tests/helpers/cross_layer_linter.py (DevPlan 163 W-D D2) для LOC-бюджета
##           <200 в основном линтере. Shell-специфика (import-linter Python-импорты не покрывает).
## @invariants
##   - invoke_module_interface с валидным интерфейсом и module.yaml — PASS
##   - Прямой bash/source/. modules/ без invoke — RED; переменные-аргументы — warning
##   - Валидные интерфейсы: healthcheck|install|deploy-hook|remove-hook
## @rationale  Python-импорты internal→modules разрешены layers-контрактом (.importlinter);
##             shell-вызовы требуют typed contract — кастомный Gate #8 v2 (историческая
##             гарантия Gate #8, DevPlan 116 B11).
## @changes  2026-08-13 | DevPlan 163 W-D D2 — извлечён из cross_layer_linter.py (881 split)
# endregion MODULE_CONTRACT

import logging
import re
from pathlib import Path

from tests.helpers.cross_layer_vars import CORE_DIR

logger = logging.getLogger(__name__)

_VALID_INTERFACES = {"healthcheck", "install", "deploy-hook", "remove-hook"}


def _detect_direct_module_calls(source_file: Path) -> list[tuple[int, str, str]]:
    """Direct bash/source/. calls to modules/ from internal/ without invoke_module_interface."""
    calls: list[tuple[int, str, str]] = []
    try:
        content = source_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return calls
    for i, stripped in enumerate((ln.strip() for ln in content.split("\n")), 1):
        if not stripped or stripped.startswith("#") or "invoke_module_interface" in stripped:
            continue
        for cmd in ["bash", "sh", "/bin/bash", "/bin/sh"]:
            m = re.search(rf"(?:^|\s)(?:{cmd})\s+(\S+)", stripped)
            if m and (
                "modules/" in m.group(1)
                or (m.group(1).startswith(("$", '"$')) and _resolve_var_to_modules_path(m.group(1), content))
            ):
                kind = "bash (direct path)" if "modules/" in m.group(1) else "bash (variable → modules/)"
                calls.append((i, kind, m.group(1)))
        for rx in (r"(?:^|\s)(?:source)\s+(\S+)", r"(?:^|\s)\.\s+(\S+)"):
            m = re.search(rx, stripped)
            if m and "modules/" in m.group(1) and m.group(1) not in {'"$@"', "${@}", "$@", '".",'}:
                calls.append((i, "source/. (direct path)", m.group(1)))
    return calls


def _resolve_var_to_modules_path(var_ref: str, file_content: str) -> bool:
    """True if a variable reference was assigned from a modules/ path."""
    var_name = var_ref.strip().strip('"').lstrip("${").rstrip("}").lstrip("$")
    return bool(var_name) and bool(re.search(rf"(?:local\s+)?{re.escape(var_name)}\s*=\s*.*modules/", file_content))


def _detect_invoke_calls(source_file: Path) -> list[dict]:
    """invoke_module_interface <module> <interface> calls."""
    calls: list[dict] = []
    try:
        lines = source_file.read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return calls
    for i, stripped in enumerate((ln.strip() for ln in lines), 1):
        m = re.search(r'invoke_module_interface\s+"?([a-zA-Z0-9_-]+)"?\s+"?"?([a-zA-Z0-9_-]+)"?"?', stripped)
        if m:
            calls.append({
                "module": m.group(1),
                "interface": m.group(2),
                "lineno": i,
                "warn": m.group(1).startswith("$") or m.group(2).startswith("$"),
            })
    return calls


def _validate_interfaces(invoke_calls: list[dict], violations: list[str], source_file: Path) -> list[str]:
    """Verify invoke interface is registered in module.yaml.interfaces (Gate #8 v2)."""
    for call in invoke_calls:
        lineno, module, interface = call["lineno"], call["module"], call["interface"]
        if call.get("warn"):
            logger.warning(
                "[IMP:7][gate8-v2][warn] %s:%d — variable args, cannot statically validate", source_file, lineno
            )
            continue
        if interface not in _VALID_INTERFACES:
            violations.append(f"  {source_file}:{lineno} — [invoke] unknown interface '{interface}' for '{module}'")
            continue
        module_yaml = CORE_DIR / "modules" / module / "module.yaml"
        if not module_yaml.exists():
            violations.append(f"  {source_file}:{lineno} — [invoke] module.yaml not found for '{module}'")
            continue
        try:
            yaml_lines = module_yaml.read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            violations.append(f"  {source_file}:{lineno} — [invoke] cannot read module.yaml for '{module}'")
            continue
        in_if = False
        registered = [
            yl.strip()[1:].strip()
            for yl in yaml_lines
            if (in_if := in_if or yl.strip() == "interfaces:") and yl.strip().startswith("-")
        ]
        if interface not in registered:
            violations.append(
                f"  {source_file}:{lineno} — [invoke] interface '{interface}' not registered for '{module}'"
            )
    return violations
