"""
# GREP_SUMMARY: test template syntax gate config templates envsubst jinja mixed_syntax contract
# STRUCTURE: ▶ scan config/*.conf.template → ◇ no {{}} → ▶ scan templates/*.conf.template → ◇ no ${} → ▶ no mixed syntax → ⎋ LDD trajectory
# region MODULE_CONTRACT
## @purpose  CI gate: verify template syntax contract — config/ uses ${VAR} (envsubst), templates/ uses {{VAR}} (template_engine.py).
##           No mixing of syntaxes allowed in a single file.
## @scope    core/modules/nginx/config/*.conf.template + core/modules/nginx/templates/*.conf.template
## @invariants
##   - File in config/ must NOT contain {{ (except comments)
##   - File in templates/ must NOT contain ${ (except nginx built-in variables)
##   - No single .template file may contain both {{}} and ${}
## @rationale DRIFT-C8: mixed syntax causes silent config corruption — agent applies wrong renderer.
## @changes  2026-07-26 | DevPlan 080 TASK-8 — Created
# endregion MODULE_CONTRACT
"""

import logging
import re
from pathlib import Path

import pytest

from tests.conftest import ldd_trajectory

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NGINX_CONFIG_DIR = _PROJECT_ROOT / "core" / "modules" / "nginx" / "config"
_NGINX_TEMPLATES_DIR = _PROJECT_ROOT / "core" / "modules" / "nginx" / "templates"

# Nginx built-in variables (e.g., ${host}, ${request_uri}) that are valid in config/ envsubst templates.
# These use $ prefix but are nginx runtime variables, not template engine placeholders.
_NGINX_BUILTIN_VARS = re.compile(
    r"\$\{(?:host|request_uri|uri|args|scheme|remote_addr|server_name|server_port|"
    r"http_[a-z_]+|upstream_[a-z_]+|cookie_[a-z_]+|arg_[a-z_]+|"
    r"proxy_host|proxy_port|document_root|realpath_root|"
    r"request_filename|document_uri|request_method|request_length|"
    r"request_time|status|body_bytes_sent|bytes_sent|"
    r"http_referer|http_user_agent|http_x_forwarded_for|"
    r"query_string|content_type|content_length)\}"
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _read_file(filepath: Path) -> str:
    """Read file content, return empty string if not found."""
    try:
        return filepath.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return ""


def _gather_templates(directory: Path, pattern: str = "*.conf.template") -> list[Path]:
    """Gather template files in directory matching pattern."""
    if not directory.exists():
        return []
    return sorted(directory.glob(pattern))


# ── Tests ──────────────────────────────────────────────────────────────────────


# 🧪 TRAP[TEST] · Regression · config/ templates must use envsubst ${} syntax
# · Scenario: config/*.conf.template contains {{}} → gate FAIL
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: template syntax contract changes
@pytest.mark.gate
@ldd_trajectory
def test_config_templates_use_envsubst_syntax(caplog) -> None:
    """All .conf.template files in config/ use ${} syntax (NOT {{}}).

    ## @purpose  DRIFT-C8: config/ directory uses envsubst (${}) via nginx Docker
    ##           entrypoint. Jinja2 {{}} syntax in config/ causes broken configs.
    ## @scenario  Scan all *.conf.template in config/ → assert no line
    ##           contains '{{' (except comments).
    """
    templates = _gather_templates(_NGINX_CONFIG_DIR)
    if not templates:
        pytest.skip(f"No .conf.template files found in {_NGINX_CONFIG_DIR}")

    violations: list[str] = []

    for tmpl in templates:
        content = _read_file(tmpl)
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            if "{{" in stripped:
                rel = tmpl.relative_to(_PROJECT_ROOT)
                violations.append(f"  {rel}:{i} → {stripped[:80]}")
                logger.warning("[IMP:8][gate][config_syntax] %s:%d contains {{}}", rel, i)

    if violations:
        logger.error("[IMP:9][gate][config_syntax] FAIL: config/ templates use {{}} syntax")
        pytest.fail(
            f"Found {len(violations)} violation(s) in config/ templates.\n"
            f"config/*.conf.template MUST use ${{VAR}} (envsubst) syntax, NOT {{{{VAR}}}} (Jinja2).\n"
            + "\n".join(violations)
        )

    logger.info("[IMP:9][gate][config_syntax] PASS: all %d config/ templates use ${} syntax", len(templates))


# 🧪 TRAP[TEST] · Regression · templates/ use Jinja2 {{}} syntax
# · Scenario: templates/*.conf.template contains ${} → gate FAIL
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: template syntax contract changes
@pytest.mark.gate
@ldd_trajectory
def test_template_templates_use_jinja_syntax(caplog) -> None:
    """All .conf.template files in templates/ use {{}} syntax (NOT ${}).

    ## @purpose  DRIFT-C8: templates/ directory uses Jinja2 ({{}}) via
    ##           template_engine.py. envsubst ${} in templates/ causes
    ##           Go/Prometheus template conflicts.
    ## @scenario  Scan all *.conf.template in templates/ → assert no line
    ##           contains '${' (except nginx built-in variables).
    """
    templates = _gather_templates(_NGINX_TEMPLATES_DIR)
    if not templates:
        pytest.skip(f"No .conf.template files found in {_NGINX_TEMPLATES_DIR}")

    violations: list[str] = []
    # Track files with multiple violations — report once per file
    seen_files: set[str] = set()

    for tmpl in templates:
        content = _read_file(tmpl)
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue
            # Check for ${} but allow nginx built-in variables
            dollar_matches = re.findall(r"\$\{[^}]+\}", stripped)
            for match in dollar_matches:
                if not _NGINX_BUILTIN_VARS.fullmatch(match):
                    rel = str(tmpl.relative_to(_PROJECT_ROOT))
                    if rel not in seen_files:
                        seen_files.add(rel)
                        violations.append(f"  {rel}:{i} → {match}")
                    logger.warning("[IMP:8][gate][template_syntax] %s:%d contains ${} — %s", rel, i, match)

    if violations:
        logger.error("[IMP:9][gate][template_syntax] FAIL: templates/ use ${} syntax")
        pytest.fail(
            f"Found {len(violations)} violation(s) in templates/.\n"
            f"templates/*.conf.template MUST use {{{{}}}} (Jinja2) syntax, NOT ${{}} (envsubst).\n"
            + "\n".join(violations)
        )

    logger.info("[IMP:9][gate][template_syntax] PASS: all %d templates/ use {{{{}}}} syntax", len(templates))


# 🧪 TRAP[TEST] · Regression · No single file mixes both {{}} and ${}
# · Scenario: any file contains both syntax markers → gate FAIL
# · Last fail: N/A (new test for DevPlan 080)
# · Remove if: mixed syntax rule changes
@pytest.mark.gate
@ldd_trajectory
def test_no_mixed_syntax_in_single_file(caplog) -> None:
    """No single .template file contains both {{}} and ${} syntax.

    ## @purpose  Preventing mixed syntax in any nginx template regardless of
    ##           directory. A file with both ${} and {{}} is a sign of template
    ##           engine confusion — the wrong renderer will silently corrupt config.
    ## @scenario  Scan ALL *.conf.template files in both config/ and templates/ →
    ##           assert no file has both {{}} and ${}.
    """
    all_templates = _gather_templates(_NGINX_CONFIG_DIR) + _gather_templates(_NGINX_TEMPLATES_DIR)
    if not all_templates:
        pytest.skip("No .conf.template files found")

    violations: list[str] = []

    for tmpl in all_templates:
        content = _read_file(tmpl)
        has_jinja = False
        has_envsubst = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "{{" in stripped:
                has_jinja = True
            if "${" in stripped:
                # Check if it's a nginx built-in — exclude those
                dollar_matches = re.findall(r"\$\{[^}]+\}", stripped)
                for m in dollar_matches:
                    if not _NGINX_BUILTIN_VARS.fullmatch(m):
                        has_envsubst = True
                        break
        if has_jinja and has_envsubst:
            rel = str(tmpl.relative_to(_PROJECT_ROOT))
            violations.append(f"  {rel}")
            logger.warning("[IMP:8][gate][mixed_syntax] %s has both {{{{}}}} and ${{}} syntax", rel)

    if violations:
        logger.error("[IMP:9][gate][mixed_syntax] FAIL: files with mixed syntax found")
        pytest.fail(
            f"Found {len(violations)} file(s) with mixed {{{{}}}} and ${{}} syntax.\n"
            f"Each .template file must use ONLY ONE syntax (Jinja2 OR envsubst), never both.\n"
            + "\n".join(violations)
        )

    logger.info("[IMP:9][gate][mixed_syntax] PASS: no mixed syntax in %d template files", len(all_templates))
