#!/usr/bin/env python3
# GREP_SUMMARY: sudoers-generator, visudo, template-render, role-mapping, batch-sudoers, NOPASSWD
# STRUCTURE: ┌argparse→action dispatch┐ → ◇ template render (template_engine.render_template native) → ◇ parse lines (role action path) → ◇ role→username mapping → ⊕ format sudoers rules → ◇ visudo -c validate → ⎷ atomic write
# region MODULE_CONTRACT
## @purpose  Extract sudoers generation from deploy-modules.sh into typed Python.
##           Generates /etc/sudoers.d/ files from sudo-whitelist.template via template_engine.render_template,
##           validates with visudo -c, writes atomically (temp → mv).
## @scope    Three operations: per-module generate, render-only (for batch collection), batch all-modules.
##           CLI entrypoint for shell scripts (deploy-modules.sh wrapper).
## @invariants
##   - Template rendering is native: template_engine.render_template(dry_run=True) — no subprocess, no temp file
##   - Only lines with action matching `make:*` produce sudoers entries
##   - Comment lines (starting with #) and blank lines are skipped
##   - Role→username mapping: owner→platform, agent→platform-agent, ci→ci-deploy, monitor→platform-monitor
##   - Unknown roles are used as-is (pass-through)
##   - visudo -c -f validates before any write; on failure, temp file is cleaned up, original untouched
##   - Sudoers files are written with mode 0440 (root readable only)
##   - No git operations — pure template render + sudoers generation
## @rationale Strangler-Fig decomposition of deploy-modules.sh (1664 lines, W4-E1).
##            Python enables typed contracts, testable parsing, and LDD telemetry.
##            Native import of template_engine (DevPlan 094) removes the last
##            subprocess call to the shell wrapper — in-process render, no temp-file dance.
## @changes
##   2026-07-22 · Created from deploy-modules.sh render_sudoers_rules, generate_module_sudoers, batch_generate_sudoers
##   2026-07-31 · DevPlan 094 Wave 2.B: subprocess bash wrapper → native template_engine.render_template
##   2026-08-13 · DevPlan 160 W4a: import-time env PLATFORM_ROOT убран (sys.path — чистая
##               относительная деривация); main() → main(argv) — argv-канон core/
##   2026-08-13 · DevPlan 160 E3: generate_module_sudoers/batch_generate_sudoers +sudoers_dir,
##               _write_sudoers_file +validator (keyword-only DI для тестов)
##   2026-08-14 · DevPlan 167 D3: main() +handlers DI-namespace (CLI dispatch-тесты без monkeypatch)
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# ── template_engine native import (DevPlan 094 — 0 subprocess) ──────────────
# Invocation: `python3 ${SCRIPT_DIR}/deploy/sudoers_generator.py` (direct script).
# sys.path[0] = core/internal/bootstrap/deploy/ — template_engine.py is 4 levels up.
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · sys.path fallback depth — 4 levels up, not 3
# · Symptom: direct-script invocation raised ModuleNotFoundError: No module named 'core'
# · Root: deploy/ → ../../.. resolves to core/ (3 levels); platform root requires 4 (deploy→bootstrap→internal→core→root)
# · Fix: join("..","..","..","..") — verified via python3 deploy/sudoers_generator.py --help
# · Prevention: invocation-mode smoke tests (direct script + module) are part of the import contract
# W4a (DevPlan 160 T4.1): import-time env-чтение PLATFORM_ROOT УБРАНО — sys.path bootstrap
# использует ЧИСТУЮ относительную деривацию (в прод-лейауте == env-значению /opt/platform).
# Env PLATFORM_ROOT остаётся доступен составному корню (main) через AppConfig.from_env().
# 🧐 TRAP[DECISION] · 2026-08-13 · — · sys.path bootstrap: env-PLATFORM_ROOT-override удалён
# · Rejected: сохранить env-чтение для sys.path (риск: import-time env, W4a-цель не достигнута)
# · Reason: деривация 4×.. канонична (deploy→bootstrap→internal→core→root); shell-обёртка
# ·   deploy-modules.sh передаёт --platform-root явно (CLI-дефолт — platform_remote_base()).
# · Rev: если появится вызов с non-стандартным PLATFORM_ROOT без --platform-root → вернуть env-чтение.
# ⚠️ TRAP[BUG] · 2026-08-13 · P1 · Path-объект в sys.path — латентный краш standalone-запуска (DevPlan 163 W-G)
# · Symptom: не проявлялся (shell-обёртки передают --platform-root); python3 sudoers_generator.py
# ·   → ModuleNotFoundError (класс discover_modules/sync_env_defaults, 163)
# · Root: os.path.join(Path(...), ...) возвращает Path; sys.path требует str
# · Fix: str(_PLATFORM_ROOT) в sys.path.insert
# · Prevention: запрет не-str в sys.path.insert (TID251-кандидат, files/ruff_policy.md)
_PLATFORM_ROOT = os.path.join(Path(Path(__file__).resolve()).parent, "..", "..", "..", "..")
if str(_PLATFORM_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_ROOT))
# Единый реестр таймаутов (DevPlan 117 D28) — visudo валидация: SUDOERS_CMD_TIMEOUT=15
# B3: канонический platform root — shared/deploy_paths (литерал /opt/platform удалён)
# DevPlan 119 E5: атомарная запись — единый канон shared/atomic_writer (validator=visudo).
from typing import Protocol, cast

from core.internal.shared.atomic_writer import atomic_write as _atomic_write
from core.internal.shared.deploy_paths import platform_remote_base
from core.internal.shared.timeouts import SUDOERS_CMD_TIMEOUT
from core.internal.template_engine import TemplateError, render_template

# ── Logging ─────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

_ROLE_USERNAME_MAP: dict[str, str] = {
    "owner": "platform",
    "agent": "platform-agent",
    "ci": "ci-deploy",
    "monitor": "platform-monitor",
}

_MAKE_BIN: str = "/usr/bin/make"
_SUDOERS_MODE: int = 0o440


# ── Helpers ─────────────────────────────────────────────────────────────────


_LINE_PARTS_MIN: int = 2  # sudoers-строка: role action path


def _map_role_to_username(role: str) -> str:
    """
    Map a role string to the corresponding system username.

    @param role: Role string from sudo-whitelist template (owner, agent, ci, monitor, or custom).
    @returns: Mapped system username. Unknown roles are returned as-is (pass-through).
    """
    # region FUNC__map_role_to_username
    ## @purpose  Map sudo-whitelist role to system username for sudoers entries
    ## @io       role: str → str (username)
    ## @complexity O(1) — dict lookup with fallback
    result = _ROLE_USERNAME_MAP.get(role, role)
    logger.info("[IMP:9][_map_role_to_username] role=%s → username=%s", role, result)
    return result
    # endregion FUNC__map_role_to_username


def _render_template(
    module_name: str,
    templates_dir: Path,
    platform_root: str,
) -> str | None:
    """
    Render the sudo-whitelist template for a module via template_engine.render_template (native).

    @param module_name: Name of the module (e.g. "nginx", "postgres").
    @param templates_dir: Directory containing sudo-whitelist.template.
    @param platform_root: PLATFORM_ROOT variable value (e.g. /opt/platform).
    @returns: Rendered template text, or None if render failed.
    """
    # region FUNC__render_template
    ## @purpose  Native render via template_engine.render_template(dry_run=True) — returns str
    ##            directly, no temp-file dance, no subprocess (DevPlan 094 Wave 2.B)
    ## @io       module_name, templates_dir, platform_root → str|None
    ## @complexity O(1) — single in-process render (bounded by template size)
    template_file = templates_dir / "sudo-whitelist.template"
    if not template_file.is_file():
        logger.error("[IMP:9][_render_template] Template not found: %s", template_file)
        return None

    try:
        rendered_text = render_template(
            str(template_file),
            vars={"MODULE_NAME": module_name, "PLATFORM_ROOT": platform_root},
            dry_run=True,
        )
    except TemplateError as exc:
        logger.error(
            "[IMP:9][_render_template] Template render FAILED: %s (unresolved: %s)",
            exc,
            exc.unresolved,
        )
        return None
    except OSError as exc:
        logger.error("[IMP:9][_render_template] Template render EXCEPTION: %s", exc)
        return None

    if rendered_text is None:
        logger.error("[IMP:9][_render_template] Template render returned None")
        return None

    logger.info("[IMP:9][_render_template] Template rendered OK (%d bytes)", len(rendered_text))
    return rendered_text
    # endregion FUNC__render_template


def _parse_rendered_lines(rendered_text: str) -> list[str]:
    """
    Parse rendered template text into sudoers rule strings.

    Skips comment lines and blank lines. Only handles actions matching `make:*`.
    Each parsed line generates: `<username> ALL=(root) NOPASSWD: /usr/bin/make -C <module_abs_dir> <target>`

    @param rendered_text: Rendered template text from template_engine.render_template.
    @returns: List of sudoers rule strings (may be empty).
    """
    # region FUNC__parse_rendered_lines
    ## @purpose  Parse rendered template lines into sudoers rule strings
    ## @io       rendered_text: str → List[str] of sudoers rules
    ## @complexity O(n) where n = number of lines in rendered text
    rules: list[str] = []
    line_num = 0

    for line in rendered_text.splitlines():
        line_num += 1
        stripped = line.strip()

        # Skip comments and blank lines
        if not stripped or stripped.startswith("#"):
            continue

        # Parse: <role> <action> <path> (path is ignored for sudoers output)
        parts = stripped.split(None, 2)  # max split: role, action, path
        if len(parts) < _LINE_PARTS_MIN:
            logger.debug("[IMP:7][_parse_rendered_lines] Line %d: skipping malformed: %s", line_num, line)
            continue

        role, action = parts[0], parts[1]

        if not role or not action:
            continue

        if not action.startswith("make:"):
            logger.debug("[IMP:7][_parse_rendered_lines] Line %d: skipping non-make action: %s", line_num, action)
            continue

        target = action[len("make:") :]
        if not target:
            logger.warning("[IMP:8][_parse_rendered_lines] Line %d: empty make target, skipping", line_num)
            continue

        username = _map_role_to_username(role)
        rule = f"{username} ALL=(root) NOPASSWD: {_MAKE_BIN} -C {{MODULE_DIR}} {target}"

        logger.debug("[IMP:7][_parse_rendered_lines] Line %d: parsed rule → %s", line_num, rule)
        rules.append(rule)

    logger.info("[IMP:9][_parse_rendered_lines] Parsed %d sudoers rules from rendered template", len(rules))
    return rules
    # endregion FUNC__parse_rendered_lines


# ── Public API ──────────────────────────────────────────────────────────────


def render_sudoers_rules(
    module_name: str,
    modules_dir: Path,
    templates_dir: Path,
    platform_root: str,
) -> list[str]:
    """
    Extract sudoers rule text for one module (template render + rule generation).

    Renders the sudo-whitelist template via template_engine.render_template, parses the output,
    and returns a list of sudoers rule strings. Does NOT validate with visudo
    and does NOT write to /etc/sudoers.d/ — this is a pure render function for
    batch collection.

    @param module_name: Name of the module.
    @param modules_dir: Path to modules directory (e.g. core/modules/).
    @param templates_dir: Path to templates directory (e.g. core/templates/).
    @param platform_root: PLATFORM_ROOT value.
    @returns: List of sudoers rule strings. Empty list on failure.
    """
    # region FUNC_render_sudoers_rules
    ## @purpose  Render + parse template into sudoers rules, no I/O write
    ## @io       module_name, modules_dir, templates_dir, platform_root → List[str]
    ## @complexity O(1) — single template render + parse
    ## @invariants
    ##   - Does NOT validate with visudo
    ##   - Does NOT write to /etc/sudoers.d/
    ##   - Returns empty list on render failure (caller must handle)
    logger.info("[IMP:8][render_sudoers_rules] START module=%s", module_name)

    rendered_text = _render_template(module_name, templates_dir, platform_root)
    if rendered_text is None:
        logger.warning("[IMP:9][render_sudoers_rules] Template render returned None for %s", module_name)
        return []

    rules = _parse_rendered_lines(rendered_text)

    # Replace {{MODULE_DIR}} placeholder with actual module absolute path
    module_abs_dir = (modules_dir / module_name).resolve()
    resolved_rules: list[str] = []
    for rule in rules:
        resolved = rule.replace("{MODULE_DIR}", str(module_abs_dir))
        resolved_rules.append(resolved)

    logger.info(
        "[IMP:9][render_sudoers_rules] DONE module=%s count=%d",
        module_name,
        len(resolved_rules),
    )
    return resolved_rules
    # endregion FUNC_render_sudoers_rules


def generate_module_sudoers(
    module_name: str,
    modules_dir: Path,
    templates_dir: Path,
    platform_root: str,
    *,
    sudoers_dir: str | None = None,
) -> bool:
    """
    Generate a per-module sudoers file at /etc/sudoers.d/platform-<module_name>.

    Renders template → parses rules → validates with visudo -c -f → atomic mv.

    @param module_name: Name of the module.
    @param modules_dir: Path to modules directory.
    @param templates_dir: Path to templates directory.
    @param platform_root: PLATFORM_ROOT value.
    @param sudoers_dir: Override для target-директории (E3, DevPlan 160 — DI для тестов:
        None = /etc/sudoers.d, поведение по умолчанию неизменно).
    @returns: True if sudoers file was generated and validated successfully, False otherwise.
    """
    # region FUNC_generate_module_sudoers
    ## @purpose  Per-module sudoers generation with visudo validation and atomic write
    ## @io       module_name, modules_dir, templates_dir, platform_root,
    ##          sudoers_dir: str | None (E3 DI) → bool
    ## @complexity O(1) — render → parse → validate → mv
    ## @invariants
    ##   - visudo -c -f MUST pass before any write
    ##   - On visudo failure: original sudoers untouched, temp file cleaned up
    ##   - Final file mode is 0440 (root readable only)
    ##   - Write is atomic: temp file in same directory → os.rename()
    logger.info("[IMP:8][generate_module_sudoers] START module=%s", module_name)

    if sudoers_dir is not None:
        sudoers_file = Path(sudoers_dir) / f"platform-{module_name}"
    else:
        sudoers_file = Path(f"/etc/sudoers.d/platform-{module_name}")

    rules = render_sudoers_rules(module_name, modules_dir, templates_dir, platform_root)
    if not rules:
        logger.warning("[IMP:9][generate_module_sudoers] No rules generated for %s", module_name)
        return False

    # Build header + rules content
    if _write_sudoers_file(sudoers_file, rules, module_name):
        logger.info("[IMP:9][generate_module_sudoers] DONE module=%s", module_name)
        return True
    logger.error("[IMP:9][generate_module_sudoers] FAILED to write sudoers for %s", module_name)
    return False
    # endregion FUNC_generate_module_sudoers


def _write_sudoers_file(
    target_path: Path,
    rules: list[str],
    module_name: str,
    *,
    validator: Callable[[str], bool] | None = None,
) -> bool:
    """
    Write sudoers rules to a temp file, validate with visudo, then atomically rename.

    @param target_path: Target path (e.g. /etc/sudoers.d/platform-nginx).
    @param rules: List of sudoers rule strings.
    @param module_name: Module name for header comment.
    @param validator: Callable(tmp_path) -> bool (E3, DevPlan 160 — DI для тестов;
        None = _validate_with_visudo, поведение по умолчанию неизменно).
    @returns: True if written and validated, False otherwise.
    """
    # region FUNC__write_sudoers_file
    ## @purpose  Atomic sudoers write via shared atomic_writer (E5) + visudo validator:
    ##           temp → chmod 0440 → visudo -c -f → os.replace.
    ## @io       target_path, rules, module_name, validator: Callable | None (E3 DI) → bool
    ## @complexity O(n) where n = number of rules (validate + write)
    ## @invariants
    ##   - Visudo validation blocks write on syntax error (validator param, E5)
    ##   - Temp file is cleaned up on failure (atomic_writer contract)
    ##   - Target directory (/etc/sudoers.d/) must exist
    logger.info("[IMP:8][_write_sudoers_file] Writing to %s (%d rules)", target_path, len(rules))

    validator = validator or _validate_with_visudo

    # Write to temp file in same directory for atomic rename
    parent_dir = target_path.parent
    os.makedirs(parent_dir, mode=0o755, exist_ok=True)

    content = (
        f"# platform module sudoers — {module_name}\n"  # pyright: ignore[reportImplicitStringConcatenation] — W11: конкатенация смежных f-строк (стиль, эквивалент ISC)
        "# Generated by sudoers_generator.py\n"
        "# Source: templates/sudo-whitelist.template\n"
        "# DO NOT edit manually — managed by core bootstrap\n"
        "\n" + "\n".join(rules) + "\n"
    )

    try:
        _ = _atomic_write(
            target_path,
            content,
            mode=_SUDOERS_MODE,
            validator=validator,
        )
        logger.info("[IMP:9][_write_sudoers_file] DONE: %s written and validated", target_path)

    except OSError as exc:
        logger.error("[IMP:10][_write_sudoers_file] OS error: %s", exc)
        return False
    # ruff: ignore[BLE001] — defensive catch-all after OSError — silent sudoers write failure недопустим (DEPLOY_BEST...
    except Exception as exc:  # noqa: EXC — catch-all after OSError, prevents silent write failure (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.error("[IMP:9][_write_sudoers_file] Unexpected error: %s", exc)
        return False
    else:
        return True


# endregion FUNC__write_sudoers_file


def _validate_with_visudo(tmp_path: str) -> bool:
    """
    Validate a sudoers file with visudo -c -f.

    @param tmp_path: Path to the temp sudoers file.
    @returns: True if validation passes, False otherwise.
    """
    # region FUNC__validate_with_visudo
    ## @purpose  Run visudo -c -f to validate sudoers syntax before atomic move
    ## @io       tmp_path: str → bool
    ## @complexity O(1) — single subprocess call
    ## @invariants
    ##   - On failure: stderr is logged at IMP:9
    ##   - Returns False, never raises
    try:
        result = subprocess.run(
            ["visudo", "-c", "-f", tmp_path], capture_output=True, text=True, timeout=SUDOERS_CMD_TIMEOUT, check=False
        )
        if result.returncode == 0:
            logger.info("[IMP:9][_validate_with_visudo] OK: %s", tmp_path)
            return True
        logger.error(
            "[IMP:9][_validate_with_visudo] FAILED: %s — stderr: %s",
            tmp_path,
            result.stderr.strip(),
        )
    except FileNotFoundError:
        logger.warning("[IMP:9][_validate_with_visudo] visudo not found — SKIPPING validation")
        # If visudo is not available (e.g. dev/test env), allow pass-through
        return True
    except subprocess.TimeoutExpired:
        logger.error("[IMP:9][_validate_with_visudo] TIMEOUT (>15s): %s", tmp_path)
        return False
    except (subprocess.CalledProcessError, OSError) as exc:
        logger.error("[IMP:9][_validate_with_visudo] Error: %s", exc)
        return False
    else:
        return False
    # endregion FUNC__validate_with_visudo


def batch_generate_sudoers(
    module_names: list[str],
    modules_dir: Path,
    templates_dir: Path,
    platform_root: str,
    *,
    sudoers_dir: str | None = None,
) -> bool:
    """
    Generate a single /etc/sudoers.d/platform-modules file for ALL modules.

    Collects rules from render_sudoers_rules() for each module, validates once
    with visudo -c, and writes atomically.

    @param module_names: List of module names.
    @param modules_dir: Path to modules directory.
    @param templates_dir: Path to templates directory.
    @param platform_root: PLATFORM_ROOT value.
    @param sudoers_dir: Override для target-директории (E3, DevPlan 160 — DI для тестов;
        None = /etc/sudoers.d, поведение по умолчанию неизменно).
    @returns: True if batch file was written and validated, False otherwise.
    """
    # region FUNC_batch_generate_sudoers
    ## @purpose  Batch all-modules sudoers generation (one file, one visudo validation)
    ## @io       module_names: List[str], modules_dir, templates_dir, platform_root,
    ##          sudoers_dir: str | None (E3 DI) → bool
    ## @complexity O(n) where n = number of modules (n template renders + 1 visudo)
    ## @invariants
    ##   - If module_names is empty → returns True (no-op)
    ##   - Individual module render failures are tolerated (collected with 'or true')
    ##   - Single visudo validation for the whole batch
    if not module_names:
        logger.info("[IMP:9][batch_generate_sudoers] No modules — skipping")
        return True

    logger.info(
        "[IMP:8][batch_generate_sudoers] START: generating batch sudoers for %d modules",
        len(module_names),
    )

    if sudoers_dir is not None:
        target_path = Path(sudoers_dir) / "platform-modules"
    else:
        target_path = Path("/etc/sudoers.d/platform-modules")
    all_rules: list[str] = []

    for mod_name in module_names:
        rules = render_sudoers_rules(mod_name, modules_dir, templates_dir, platform_root)
        if rules:
            all_rules.extend(rules)
            logger.debug("[IMP:7][batch_generate_sudoers] Module %s: %d rules", mod_name, len(rules))
        else:
            logger.info(
                "[IMP:8][batch_generate_sudoers] Module %s: no rules (render skipped or empty)",
                mod_name,
            )

    if not all_rules:
        logger.warning("[IMP:9][batch_generate_sudoers] No rules collected from any module")
        return False

    logger.info("[IMP:8][batch_generate_sudoers] Total rules: %d", len(all_rules))
    success = _write_sudoers_file(target_path, all_rules, "platform-modules")
    if success:
        logger.info("[IMP:9][batch_generate_sudoers] DONE: batch sudoers generated for %d modules", len(module_names))
    else:
        logger.error("[IMP:9][batch_generate_sudoers] FAILED: batch sudoers write failed")
    return success
    # endregion FUNC_batch_generate_sudoers


# ── CLI Entrypoint ──────────────────────────────────────────────────────────


class _CliArgs(Protocol):
    """Typed argparse-namespace контракт (W11: reportAny=error — Namespace-атрибуты Any).

    ## @purpose  Типизация CLI-аргументов через Protocol (БЕЗ runtime-атрибутов — обход
    ##            hasattr-гейта argparse: class-атрибут со значением заставляет parse_args
    ##            пропускать parser-default для dest; Protocol-cast runtime не создаёт).
    ## @invariants  Поля = имена dest'ов argparse (всегда устанавливаются parser'ом).
    """

    action: str
    module_name: str | None
    module_names: str | None
    modules_dir: str | None
    templates_dir: str | None
    platform_root: str


def main(argv: list[str] | None = None, *, handlers: object | None = None) -> int:
    """
    CLI entrypoint for sudoers_generator.py.

    Usage:
        sudoers_generator.py --action {generate,batch-generate,render-rules} \\
            [--module-name NAME] [--module-names NAME1,NAME2,...] \\
            [--modules-dir PATH] [--templates-dir PATH] [--platform-root PATH]

    DevPlan 167 D3 (DI-канон 163 W-H): `handlers` — опциональный namespace с
    generate_module_sudoers/render_sudoers_rules/batch_generate_sudoers (helper-namespace
    injection для CLI dispatch-тестов, 0 monkeypatch.setattr). None — module-level fallback,
    прод-поведение без изменений.
    """
    # region FUNC_main
    ## @purpose  CLI dispatch for shell script integration (W4a: argv-канон main(argv);
    ##            167 D3: +handlers DI-namespace)
    ## @io       ⇥ argv: list[str] | None (None = sys.argv[1:]), handlers: object | None (DI)
    ##           → exit code (0=OK, 1=error)
    ## @complexity O(1) — argument parsing + dispatch
    # 167 D3: partial-namespace поддержан (getattr + fallback) — тест передаёт только
    # нужный handler, остальные — module-level (0 monkeypatch.setattr)
    if handlers is not None:
        generate_fn = getattr(handlers, "generate_module_sudoers", generate_module_sudoers)
        render_fn = getattr(handlers, "render_sudoers_rules", render_sudoers_rules)
        batch_fn = getattr(handlers, "batch_generate_sudoers", batch_generate_sudoers)
    else:
        generate_fn = generate_module_sudoers
        render_fn = render_sudoers_rules
        batch_fn = batch_generate_sudoers
    parser = argparse.ArgumentParser(
        description="Generate sudoers rules from sudo-whitelist template",
    )
    _ = parser.add_argument(
        "--action",
        required=True,
        choices=["generate", "batch-generate", "render-rules"],
        help="Action to perform",
    )
    _ = parser.add_argument(
        "--module-name",
        help="Module name (required for generate, render-rules)",
    )
    _ = parser.add_argument(
        "--module-names",
        help="Comma-separated module names (required for batch-generate)",
    )
    _ = parser.add_argument(
        "--modules-dir",
        default=None,
        help="Path to modules directory (default: auto-detect from platform-root)",
    )
    _ = parser.add_argument(
        "--templates-dir",
        default=None,
        help="Path to templates directory (default: auto-detect from platform-root)",
    )
    platform_root_default = str(platform_remote_base())
    _ = parser.add_argument(
        "--platform-root",
        default=platform_root_default,
        help=f"Platform root directory (default: {platform_root_default})",
    )

    # W11: parse_args → Namespace (атрибуты Any) — Protocol-cast через object (см. _CliArgs)
    args = cast(_CliArgs, cast(object, parser.parse_args(argv)))

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="[IMP:%(levelno)s][%(name)s][%(funcName)s] %(message)s",
        stream=sys.stderr,
    )

    # Resolve default paths from platform-root if not explicitly provided
    platform_root = args.platform_root
    modules_dir = Path(args.modules_dir) if args.modules_dir else Path(platform_root) / "core" / "modules"

    templates_dir = Path(args.templates_dir) if args.templates_dir else Path(platform_root) / "core" / "templates"

    # Validate paths
    if not modules_dir.is_dir():
        logger.error("[IMP:10][main] Modules directory not found: %s", modules_dir)
        return 1
    if not templates_dir.is_dir():
        logger.error("[IMP:10][main] Templates directory not found: %s", templates_dir)
        return 1

    # Dispatch
    if args.action in {"generate", "render-rules"}:
        if not args.module_name:
            logger.error("[IMP:10][main] --module-name is required for action=%s", args.action)
            return 1

        if args.action == "generate":
            success = generate_fn(
                args.module_name,
                modules_dir,
                templates_dir,
                platform_root,
            )
        else:
            rules = render_fn(
                args.module_name,
                modules_dir,
                templates_dir,
                platform_root,
            )
            for rule in rules:
                print(rule)
            success = True

    elif args.action == "batch-generate":
        if not args.module_names:
            logger.error("[IMP:10][main] --module-names is required for batch-generate")
            return 1

        module_names = [m.strip() for m in args.module_names.split(",") if m.strip()]
        success = batch_fn(
            module_names,
            modules_dir,
            templates_dir,
            platform_root,
        )

    else:
        logger.error("[IMP:10][main] Unknown action: %s", args.action)
        return 1

    if success:
        logger.info("[IMP:9][main] Action '%s' completed successfully", args.action)
    else:
        logger.error("[IMP:10][main] Action '%s' FAILED", args.action)
    return 0 if success else 1
    # endregion FUNC_main


if __name__ == "__main__":
    sys.exit(main())
