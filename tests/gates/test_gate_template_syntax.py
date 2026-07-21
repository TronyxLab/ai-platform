# GREP_SUMMARY: test-gate template-syntax strict-grammar {{UPPER_SNAKE}} legacy-drift ci-gate
# STRUCTURE: ┌manifest.yaml┐ → ◇ iterate templates[] → ◇ scan file for __VAR__ and ${VAR} → ⊕ assert strict grammar
# region MODULE_CONTRACT
## @purpose  Gate: verify all template files use unified {{UPPER_SNAKE}} syntax only
## @scope    Checks all files listed in template-manifest.yaml — no legacy __VAR__ or ${VAR} placeholders
## @invariants
##   - __[A-Z_]+__ patterns are FORBIDDEN (legacy sed syntax)
##   - ${[A-Z_]+} patterns are FORBIDDEN except in compose files (runtime vars like ${IMAGE_REGISTRY})
##   - {{[A-Z][A-Z0-9_]*}} is the only allowed placeholder syntax
##   - compose files may contain ${VAR:-default} for runtime Docker Compose variables
## @rationale Part of template unification — one syntax to rule them all
## @usecases make gate MODE=fast runs this automatically via @pytest.mark.gate
# endregion MODULE_CONTRACT

import os
import re

import pytest
from conftest import ldd_trajectory
from tests.helpers.gate_helpers import repo_root



# Compose runtime vars that are ALLOWED (Docker Compose runtime substitution)
# These are NOT template placeholders — they are resolved at compose up time
ALLOWED_COMPOSE_RUNTIME_VARS = re.compile(r"\$\{[A-Z_]+[^}]*\}")

# Forbidden legacy syntax patterns
LEGACY_DOUBLE_UNDERSCORE = re.compile(r"__[A-Z_]+__")

# Template engine syntax — the only allowed grammar
TEMPLATE_ENGINE_VAR = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")


@pytest.mark.gate
@ldd_trajectory
# region FUNC_test_all_templates_use_strict_grammar
## @purpose — No __VAR__ or ${VAR} in template-manifest files
## @io — ⎋ PASS/FAIL with file:line diagnostics
## @complexity O(f * l) where f = files, l = lines per file
## 🧪 TRAP[TEST] · 2026-07-18 · REGRESSION · Template syntax drift prevention
## · Last fail: N/A (preventive)
## · Remove if: all templates have been migrated to {{UPPER_SNAKE}} and legacy detection is no longer needed
def test_all_templates_use_strict_grammar(caplog):
    """Verify all template files use unified {{UPPER_SNAKE}} syntax — no legacy __VAR__ or ${VAR} except compose runtime vars."""
    import logging

    logger = logging.getLogger(__name__)

    manifest_path = os.path.join(repo_root(), "core", "templates", "template-manifest.yaml")
    if not os.path.exists(manifest_path):
        logger.critical("[IMP:9][gate][template-syntax] Manifest not found: %s", manifest_path)
        pytest.fail(f"template-manifest.yaml not found at {manifest_path}")

    try:
        import yaml
    except ImportError:
        pytest.skip("PyYAML not available")

    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    if not manifest or "templates" not in manifest or not manifest["templates"]:
        logger.critical("[IMP:9][gate][template-syntax] No templates in manifest — skipping")
        return

    errors: list[str] = []
    manifest_dir = os.path.dirname(os.path.abspath(manifest_path))

    for entry in manifest["templates"]:
        tmpl_path = entry["template"]
        abs_tmpl_path = os.path.join(manifest_dir, tmpl_path) if not os.path.isabs(tmpl_path) else tmpl_path

        if not os.path.exists(abs_tmpl_path):
            errors.append(f"MISSING: {tmpl_path}")
            continue

        # For directory type, check recursively
        if entry.get("type") == "directory":
            if not os.path.isdir(abs_tmpl_path):
                errors.append(f"MISSING_DIR: {tmpl_path}")
                continue
            _check_directory(abs_tmpl_path, tmpl_path, errors, is_dual_role=entry.get("dual_role", False))
        else:
            _check_file(abs_tmpl_path, tmpl_path, errors, is_dual_role=entry.get("dual_role", False))

    if errors:
        error_msg = "\n".join(errors)
        logger.critical("[IMP:9][gate][template-syntax] %d syntax issue(s) found", len(errors))
        pytest.fail(f"Template syntax violations:\n{error_msg}")

    logger.critical("[IMP:9][gate][template-syntax] All templates use strict grammar")


# endregion


def _is_binary(filepath: str) -> bool:
    """Check if a file is binary (e.g. .pyc, .pyo, .png)."""
    _, ext = os.path.splitext(filepath)
    return ext in (".pyc", ".pyo", ".png", ".jpg", ".ico", ".gif", ".woff", ".woff2", ".ttf", ".eot")


def _is_dual_role_file(display_path: str) -> bool:
    """Check if a file uses ${VAR} for Docker envsubst compatibility (dual-role).

    Nginx template files use ${PLATFORM_DOMAIN} for both Docker envsubst (container
    entrypoint) and bare-metal sed rendering. These are NOT migrated to {{VAR}}
    because envsubst only handles ${VAR} syntax.
    """
    return "nginx" in display_path and ".template" in display_path


def _is_readme_documentation(display_path: str) -> bool:
    """Check if a file is README documentation (not a template file per se).

    README files may document legacy variable names for reference purposes.
    """
    return "README.md" in display_path


def _check_file(filepath: str, display_path: str, errors: list[str], *, is_dual_role: bool = False) -> None:
    """Check a single file for forbidden syntax patterns.

    Args:
        filepath: Absolute path to the file
        display_path: Display path for error messages
        errors: Error list to append to
        is_dual_role: If True, file is both template AND live config (e.g. alert-rules.yml)
    """
    # Skip binary files (__pycache__, images, etc.)
    if _is_binary(filepath):
        return

    # Skip files in __pycache__ directories
    if "__pycache__" in filepath.split(os.sep):
        return

    is_compose = filepath.endswith("docker-compose.yml")
    is_nginx_dual = _is_dual_role_file(display_path)
    is_readme = _is_readme_documentation(display_path)
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as e:
        errors.append(f"READ_ERR: {display_path}: {e}")
        return

    for line_no, line in enumerate(lines, 1):
        stripped = line.rstrip("\n")

        # Check for legacy __VAR__ syntax (skip README docs that reference old syntax)
        if not is_readme:
            legacy_matches = LEGACY_DOUBLE_UNDERSCORE.findall(stripped)
            if legacy_matches:
                errors.append(
                    f"LEGACY_SYNTAX: {display_path}:{line_no}: found __VAR__ syntax: {', '.join(legacy_matches)}"
                )

        # Check for ${VAR} — but allow in compose files (runtime vars) and nginx dual-role files
        if not is_compose and not is_dual_role and not is_nginx_dual:
            dollar_matches = re.findall(r"\$\{[A-Z_]+[^}]*\}", stripped)
            if dollar_matches:
                errors.append(
                    f"DOLLAR_SYNTAX: {display_path}:{line_no}: found ${{VAR}} syntax: {', '.join(dollar_matches)}"
                )


def _check_directory(dirpath: str, display_path: str, errors: list[str], *, is_dual_role: bool = False) -> None:
    """Recursively check all files in a directory."""
    for root, _dirs, files in os.walk(dirpath):
        # Skip .git directory if present
        if "/.git/" in root or root.endswith("/.git"):
            continue
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, os.path.dirname(dirpath))
            _check_file(fpath, f"{display_path}{rel}", errors, is_dual_role=is_dual_role)
