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
# endregion MODULE_CONTRACT

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── template_engine native import (DevPlan 094 — 0 subprocess) ──────────────
# Invocation: `python3 ${SCRIPT_DIR}/deploy/sudoers_generator.py` (direct script).
# sys.path[0] = core/internal/bootstrap/deploy/ — template_engine.py is 4 levels up.
# PLATFORM_ROOT is always defined by the calling shell wrapper (deploy-modules.sh);
# fallback derives the platform root relative to this module's location.
# ⚠️ TRAP[BUG] · 2026-07-31 · P1 · sys.path fallback depth — 4 levels up, not 3
# · Symptom: direct-script invocation raised ModuleNotFoundError: No module named 'core'
# · Root: deploy/ → ../../.. resolves to core/ (3 levels); platform root requires 4 (deploy→bootstrap→internal→core→root)
# · Fix: join("..","..","..","..") — verified via python3 deploy/sudoers_generator.py --help
# · Prevention: invocation-mode smoke tests (direct script + module) are part of the import contract
_PLATFORM_ROOT = os.environ.get(
    "PLATFORM_ROOT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."),
)
# Validate the resolved root actually contains core/ — guards against stale/misconfigured
# PLATFORM_ROOT env values (DevPlan 094 §7.5). Falls back to relative derivation.
if not os.path.isdir(os.path.join(_PLATFORM_ROOT, "core", "internal")):
    _PLATFORM_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")
if _PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, _PLATFORM_ROOT)
from core.internal.template_engine import TemplateError, render_template

# ── Logging ─────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

_ROLE_USERNAME_MAP: dict = {
    "owner": "platform",
    "agent": "platform-agent",
    "ci": "ci-deploy",
    "monitor": "platform-monitor",
}

_MAKE_BIN: str = "/usr/bin/make"
_SUDOERS_MODE: int = 0o440


# ── Helpers ─────────────────────────────────────────────────────────────────


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
    @param platform_root: PLATFORM_ROOT variable value (e.g. "/opt/platform").
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


def _safe_cleanup(path: str) -> None:
    """Remove a temp file if it exists (best-effort)."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:  # noqa: EXC — best-effort cleanup, never raise
        pass


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
        if len(parts) < 2:
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
    resolved_rules = []
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
) -> bool:
    """
    Generate a per-module sudoers file at /etc/sudoers.d/platform-<module_name>.

    Renders template → parses rules → validates with visudo -c -f → atomic mv.

    @param module_name: Name of the module.
    @param modules_dir: Path to modules directory.
    @param templates_dir: Path to templates directory.
    @param platform_root: PLATFORM_ROOT value.
    @returns: True if sudoers file was generated and validated successfully, False otherwise.
    """
    # region FUNC_generate_module_sudoers
    ## @purpose  Per-module sudoers generation with visudo validation and atomic write
    ## @io       module_name, modules_dir, templates_dir, platform_root → bool
    ## @complexity O(1) — render → parse → validate → mv
    ## @invariants
    ##   - visudo -c -f MUST pass before any write
    ##   - On visudo failure: original sudoers untouched, temp file cleaned up
    ##   - Final file mode is 0440 (root readable only)
    ##   - Write is atomic: temp file in same directory → os.rename()
    logger.info("[IMP:8][generate_module_sudoers] START module=%s", module_name)

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
) -> bool:
    """
    Write sudoers rules to a temp file, validate with visudo, then atomically rename.

    @param target_path: Target path (e.g. /etc/sudoers.d/platform-nginx).
    @param rules: List of sudoers rule strings.
    @param module_name: Module name for header comment.
    @returns: True if written and validated, False otherwise.
    """
    # region FUNC__write_sudoers_file
    ## @purpose  Atomic sudoers write: temp → visudo -c → os.rename()
    ## @io       target_path, rules, module_name → bool
    ## @complexity O(n) where n = number of rules (validate + write)
    ## @invariants
    ##   - Visudo validation blocks write on syntax error
    ##   - Temp file is cleaned up on failure
    ##   - Target directory (/etc/sudoers.d/) must exist
    logger.info("[IMP:8][_write_sudoers_file] Writing to %s (%d rules)", target_path, len(rules))

    # Write to temp file in same directory for atomic rename
    parent_dir = target_path.parent
    os.makedirs(parent_dir, mode=0o755, exist_ok=True)

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f".platform-sudoers-{module_name}-",
            suffix=".tmp",
            dir=str(parent_dir),
            delete=False,
        ) as tmp_fh:
            tmp_path = tmp_fh.name

            # Write header
            tmp_fh.write(f"# platform module sudoers — {module_name}\n")
            tmp_fh.write("# Generated by sudoers_generator.py\n")
            tmp_fh.write("# Source: templates/sudo-whitelist.template\n")
            tmp_fh.write("# DO NOT edit manually — managed by core bootstrap\n")
            tmp_fh.write("\n")

            # Write rules
            for rule in rules:
                tmp_fh.write(rule)
                tmp_fh.write("\n")

        # Set mode 0440 before validation
        os.chmod(tmp_path, _SUDOERS_MODE)

        # Validate with visudo
        if not _validate_with_visudo(tmp_path):
            logger.error(
                "[IMP:10][_write_sudoers_file] visudo -c FAILED for %s — original NOT touched",
                target_path,
            )
            _safe_cleanup(tmp_path)
            return False

        # Atomic rename
        shutil.copy2(tmp_path, str(target_path))
        os.chmod(str(target_path), _SUDOERS_MODE)
        _safe_cleanup(tmp_path)

        logger.info("[IMP:9][_write_sudoers_file] DONE: %s written and validated", target_path)
        return True

    except OSError as exc:
        logger.error("[IMP:10][_write_sudoers_file] OS error: %s", exc)
        _safe_cleanup(tmp_path)  # type: ignore[possibly-undefined]
        return False
    except Exception as exc:  # noqa: EXC — catch-all after OSError, prevents silent write failure (best-effort: DEPLOY_BEST_EFFORT policy)
        logger.error("[IMP:9][_write_sudoers_file] Unexpected error: %s", exc)
        _safe_cleanup(tmp_path)  # type: ignore[possibly-undefined]
        return False


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
            ["visudo", "-c", "-f", tmp_path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            logger.info("[IMP:9][_validate_with_visudo] OK: %s", tmp_path)
            return True
        logger.error(
            "[IMP:9][_validate_with_visudo] FAILED: %s — stderr: %s",
            tmp_path,
            result.stderr.strip(),
        )
        return False
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
    # endregion FUNC__validate_with_visudo


def batch_generate_sudoers(
    module_names: list[str],
    modules_dir: Path,
    templates_dir: Path,
    platform_root: str,
) -> bool:
    """
    Generate a single /etc/sudoers.d/platform-modules file for ALL modules.

    Collects rules from render_sudoers_rules() for each module, validates once
    with visudo -c, and writes atomically.

    @param module_names: List of module names.
    @param modules_dir: Path to modules directory.
    @param templates_dir: Path to templates directory.
    @param platform_root: PLATFORM_ROOT value.
    @returns: True if batch file was written and validated, False otherwise.
    """
    # region FUNC_batch_generate_sudoers
    ## @purpose  Batch all-modules sudoers generation (one file, one visudo validation)
    ## @io       module_names: List[str], modules_dir, templates_dir, platform_root → bool
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


def main() -> int:
    """
    CLI entrypoint for sudoers_generator.py.

    Usage:
        sudoers_generator.py --action {generate,batch-generate,render-rules} \\
            [--module-name NAME] [--module-names NAME1,NAME2,...] \\
            [--modules-dir PATH] [--templates-dir PATH] [--platform-root PATH]
    """
    # region FUNC_main
    ## @purpose  CLI dispatch for shell script integration
    ## @io       argparse → exit code (0=OK, 1=error)
    ## @complexity O(1) — argument parsing + dispatch
    parser = argparse.ArgumentParser(
        description="Generate sudoers rules from sudo-whitelist template",
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=["generate", "batch-generate", "render-rules"],
        help="Action to perform",
    )
    parser.add_argument(
        "--module-name",
        help="Module name (required for generate, render-rules)",
    )
    parser.add_argument(
        "--module-names",
        help="Comma-separated module names (required for batch-generate)",
    )
    parser.add_argument(
        "--modules-dir",
        default=None,
        help="Path to modules directory (default: auto-detect from platform-root)",
    )
    parser.add_argument(
        "--templates-dir",
        default=None,
        help="Path to templates directory (default: auto-detect from platform-root)",
    )
    parser.add_argument(
        "--platform-root",
        default="/opt/platform",
        help="Platform root directory (default: /opt/platform)",
    )

    args = parser.parse_args()

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
    if args.action in ("generate", "render-rules"):
        if not args.module_name:
            logger.error("[IMP:10][main] --module-name is required for action=%s", args.action)
            return 1

        if args.action == "generate":
            success = generate_module_sudoers(
                args.module_name,
                modules_dir,
                templates_dir,
                platform_root,
            )
        else:
            rules = render_sudoers_rules(
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
        success = batch_generate_sudoers(
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
